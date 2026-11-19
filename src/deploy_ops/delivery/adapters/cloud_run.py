# delivery/adapters/cloud_run.py - Bind Cloud Run candidate and traffic writes to exact native operation evidence.


import copy
import json
import re
import time

import httpx

from ...config import fingerprint
from ...errors import Refused
from ..files import read_regular
from .base import need


# Use one fixed verified API origin; recorded provider names never become arbitrary URLs
ORIGIN = 'https://run.googleapis.com/v2/'
REVISION = 'TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION'
LATEST = 'TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST'
JOURNAL = 'cloud-run-operation.json'


# Bind every native call and retained record to an explicit project, region, service and local attempt
def scope(context):
    project, region, name = (need(context.options, key) for key in ('project_id', 'region', 'name'))
    if (not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]|[0-9]{6,20}', project)
            or not re.fullmatch(r'[a-z]+-[a-z]+[0-9]', region)
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}[a-z0-9]|[a-z]', name)):
        raise Refused('Cloud Run needs an explicit supported project, region and service name', 422)
    return {'project_id': project, 'region': region, 'name': name,
            'service': f'projects/{project}/locations/{region}/services/{name}',
            'release_id': context.release_id, 'component': context.name, 'environment': context.environment}


# Validate resource names before attaching the existing credential to a fixed-origin request
def resource(context, name):
    selected = scope(context)
    prefix = f"projects/{selected['project_id']}/locations/{selected['region']}/"
    if name == selected['service']:
        return name
    if (name.startswith(selected['service'] + '/revisions/') and
            re.fullmatch(r'[a-z][a-z0-9-]{0,62}', name.rsplit('/', 1)[-1])):
        return name
    if name.startswith(prefix + 'operations/') and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', name[len(prefix + 'operations/'):]):
        return name
    raise Refused('Cloud Run returned an operation or revision outside the reviewed scope', 409)


# Use the supervised native credential provider without exposing its captured token to events or files
def token(context):
    value = context.process.env.get('GOOGLE_OAUTH_ACCESS_TOKEN') or context.process.env.get('CLOUDSDK_AUTH_ACCESS_TOKEN')
    if not value:
        value = context.command(['gcloud', 'auth', 'print-access-token', '--project', scope(context)['project_id'], '--quiet'], capture=True).strip()
    if not isinstance(value, str) or not re.fullmatch(r'[^\s\x00-\x1f]{8,16384}', value):
        raise Refused('Cloud Run authentication returned no usable access token', 403)
    if value not in context.process.secrets:
        context.process.secrets.append(value)
    return value


# Bound API calls and response size, with no automatic redirects, proxies or mutation retries
def request(context, method, name, body=None, mask=None):
    name = resource(context, name)
    context.process.heartbeat()
    credential = token(context)
    context.process.heartbeat()
    remaining = context.process.deadline - time.time() if context.process.deadline else 10
    if remaining < 1:
        raise Refused('Execution authority ended before the Cloud Run request; inspect any earlier operation', 409)
    try:
        with httpx.Client(timeout=min(10, remaining), trust_env=False, follow_redirects=False) as client:
            params = {'updateMask': mask, 'allowMissing': 'false'} if method == 'PATCH' else None
            with client.stream(method, ORIGIN + name, params=params, json=body,
                               headers={'Authorization': 'Bearer ' + credential}) as response:
                if response.status_code >= 300:
                    raise Refused('Cloud Run API refused the selected request; inspect retained operation evidence before retrying',
                                  response.status_code if response.status_code in {403, 404, 409, 412, 429} else 503)
                data = bytearray()
                for part in response.iter_bytes():
                    data.extend(part)
                    if len(data) > 10_000_000:
                        raise Refused('Cloud Run response exceeded its bounded size; inspect the operation', 503)
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError('object required')
                return result
    except (httpx.HTTPError, ValueError, TypeError):
        raise Refused('Cloud Run response was unavailable; submission may be uncertain, so inspect before another write', 503) from None


# Convert native short or full revision names to the same exact service-bound identity
def revision(context, value):
    service = scope(context)['service']
    if not isinstance(value, str) or not value:
        raise Refused('Cloud Run revision identity is unavailable', 409)
    return resource(context, value if '/' in value else service + '/revisions/' + value)


# Keep split/tagged/missing routes outside this deliberately narrow two-stage contract
def route(context, service, observed=False):
    rows = service.get('trafficStatuses' if observed else 'traffic', [])
    if not rows and not observed:
        rows = [{'type': LATEST, 'percent': 100}]
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or type(rows[0].get('percent')) is not int or rows[0]['percent'] != 100 or rows[0].get('tag')):
        raise Refused('Cloud Run requires one untagged, fully observed 100 percent route', 422)
    row = rows[0]
    target = row.get('revision')
    if row.get('type') == LATEST and not observed:
        target = service.get('latestReadyRevision')
    elif row.get('type') not in {REVISION, LATEST}:
        raise Refused('Cloud Run route allocation type is unavailable', 409)
    return revision(context, target)


# ProtoJSON omits default false booleans; generation and terminal readiness still require positive evidence
def ready(service):
    return (service.get('reconciling', False) is False and service.get('generation') is not None
            and service.get('observedGeneration') == service['generation']
            and service.get('terminalCondition', {}).get('state') == 'CONDITION_SUCCEEDED')


# Hash preserved settings rather than placing environment values or secret references in operation records
def template_digest(service):
    template = copy.deepcopy(service.get('template', {}))
    template.pop('revision', None)
    return fingerprint(template)


# Freeze the physical service identity and concurrency token without retaining raw provider payloads
def snapshot(context, service):
    if (service.get('name') != scope(context)['service'] or not ready(service)
            or not isinstance(service.get('uid'), str) or not service['uid']
            or not isinstance(service.get('etag'), str) or not service['etag']):
        raise Refused('Cloud Run service identity or completed reconciliation is unavailable', 409)
    desired, observed = route(context, service), route(context, service, True)
    if desired != observed:
        raise Refused('Cloud Run desired and observed traffic differ; inspect the existing rollout', 409)
    containers = service.get('template', {}).get('containers', [])
    if not isinstance(containers, list) or len(containers) != 1 or not isinstance(containers[0], dict):
        raise Refused('Cloud Run currently supports exactly one existing container', 422)
    if service.get('buildConfig'):
        raise Refused('Cloud Run function/build configuration requires a separate reviewed workflow', 422)
    return {'uid': service['uid'], 'etag': service['etag'], 'generation': service['generation'],
            'template_digest': template_digest(service), 'traffic_digest': fingerprint(service.get('traffic', [])),
            'route': desired, 'latest_created': revision(context, service.get('latestCreatedRevision')),
            'latest_ready': revision(context, service.get('latestReadyRevision'))}


# Read the actual immutable revision image, keeping readiness and service routing separate
def revision_state(context, name, value):
    if value.get('name') != name or value.get('service') not in {scope(context)['service'], scope(context)['name']} or not value.get('uid'):
        raise Refused('Cloud Run revision scope or physical identity is unavailable', 409)
    containers = value.get('containers', [])
    if not isinstance(containers, list) or len(containers) != 1:
        raise Refused('Cloud Run revision container identity is unavailable', 409)
    image = containers[0].get('image')
    if not isinstance(image, str) or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image):
        raise Refused('Cloud Run did not expose an observed immutable revision image', 409)
    conditions = value.get('conditions', [])
    condition = next((item for item in conditions if item.get('type') == 'Ready'), {})
    return {'revision': name, 'uid': value['uid'], 'image': image,
            'ready': value.get('reconciling', False) is False and value.get('generation') is not None
                and value.get('observedGeneration') == value['generation'] and condition.get('state') == 'CONDITION_SUCCEEDED',
            'failed': condition.get('state') == 'CONDITION_FAILED'}


# Review a deterministic release-bound native request before either mutation
def prepare(context, image):
    selected = scope(context)
    service = request(context, 'GET', selected['service'])
    baseline = snapshot(context, service)
    previous = revision_state(context, baseline['route'], request(context, 'GET', baseline['route']))
    if not previous['ready']:
        raise Refused('The serving Cloud Run revision is not confirmed ready', 409)
    identity = fingerprint({'scope': selected, 'baseline': baseline, 'image': image})
    candidate = selected['name'] + '-do-' + identity[:12]
    if len(candidate) > 63:
        raise Refused('Cloud Run service name leaves no room for the bounded revision identity', 422)
    desired = copy.deepcopy(service)
    desired['template']['containers'][0]['image'] = image
    native = {'version': 1, 'scope': selected, 'baseline': baseline, 'image': image,
              'template_digest': template_digest(desired),
              'candidate': selected['service'] + '/revisions/' + candidate,
              'operation_id': identity, 'no_op': previous['image'] == image
                  and service['template']['containers'][0].get('image') == image
                  and baseline['latest_created'] == baseline['latest_ready'] == baseline['route']}
    if native['no_op']:
        native['candidate'] = baseline['route']
    return {'cloud_run_request': native, 'request_digest': fingerprint(native),
            'previous_revision': baseline['route']}


# Verify the reviewed request before consulting any journal or writing to the provider
def reviewed(context, prepared):
    native = prepared.get('cloud_run_request', {})
    if (type(native.get('version')) is not int or native.get('version') != 1 or native.get('scope') != scope(context)
            or fingerprint(native) != prepared.get('request_digest') or native.get('image') != prepared.get('image')):
        raise Refused('Cloud Run request lacks its supported reviewed operation identity; prepare a fresh release', 409)
    revision(context, native.get('candidate'))
    revision(context, native.get('baseline', {}).get('route'))
    return native


# Project only the frozen public request, never the service template or credential environment
def review(context, prepared):
    native = reviewed(context, prepared)
    return {'service': native['scope']['service'], 'service_uid': native['baseline']['uid'],
            'image': native['image'], 'baseline_revision': native['baseline']['route'],
            'candidate_revision': native['candidate'], 'traffic_percent': 100,
            'action': 'no-op' if native['no_op'] else 'update',
            'native_request_digest': prepared['request_digest'],
            'steps': ['Verify the exact serving image and route without a provider write'] if native['no_op'] else [
                'Create the exact candidate while the baseline retains all traffic',
                'Recheck candidate readiness, image and preserved configuration',
                'Conditionally switch all traffic to the candidate and verify the observed route']}


# Reuse the existing durable journal writer without creating a competing persistence owner
def record(context, value):
    from .cloud import durable_record
    durable_record(context.stage.parent / JOURNAL, value)


# Load only the selected bounded operation record; missing evidence never implies no submission
def load(context, prepared=None):
    try:
        value = json.loads(read_regular(context.stage.parent / JOURNAL, 1_000_000))
        native = value['request']
        if (native['scope'] != scope(context) or native['version'] != 1
                or fingerprint(native) != value['request_digest']
                or (prepared is not None and native != reviewed(context, prepared))):
            raise ValueError('scope')
        revision(context, native['candidate'])
        revision(context, native['baseline']['route'])
        if value['status'] not in {'candidate-submitted', 'candidate-acknowledged', 'candidate-ready',
                                   'traffic-submitted', 'traffic-acknowledged', 'converged', 'no-op'}:
            raise ValueError('phase')
        for field in ('candidate_operation', 'traffic_operation'):
            if value.get(field):
                resource(context, value[field])
                if '/operations/' not in value[field]:
                    raise ValueError('operation')
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused('No compatible Cloud Run operation record is available; submission and outcome remain unknown', 409) from None


# Store only operation identity/completion/code, never raw messages or a Service response containing secrets
def operation(context, value):
    name = resource(context, value.get('name', ''))
    if '/operations/' not in name:
        raise Refused('Cloud Run did not return a scoped native operation identity', 409)
    result = {'name': name, 'done': value.get('done') is True}
    if result['done']:
        if isinstance(value.get('error'), dict):
            result['error_code'] = value['error'].get('code') if type(value['error'].get('code')) is int else None
        elif not isinstance(value.get('response'), dict):
            raise Refused('Cloud Run operation completed without an inspectable result', 409)
    return result


# Wait only the acknowledged operation while the current worker still owns execution authority
def wait(context, name, deadline):
    while time.monotonic() < deadline:
        context.process.heartbeat()
        value = operation(context, request(context, 'GET', name))
        if value['done']:
            if 'error_code' in value:
                raise Refused('Cloud Run native operation failed; inspect its retained phase before any new write', 409)
            return
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise Refused('Cloud Run operation remains pending beyond this worker window; inspect without replaying', 409)


# Read current scope, actual candidate bytes and route without authorizing another mutation
def observe(context, value):
    native = value['request']
    operations = {}
    for key in ('candidate_operation', 'traffic_operation'):
        if value.get(key):
            operations[key] = operation(context, request(context, 'GET', value[key]))
    try:
        service = request(context, 'GET', native['scope']['service'])
    except Refused as error:
        if error.status == 404:
            return {'status': 'service-unavailable', 'operations': operations, 'ready': False}
        raise
    if service.get('name') != native['scope']['service'] or service.get('uid') != native['baseline']['uid']:
        return {'status': 'target-changed', 'operations': operations, 'ready': False}
    if any('error_code' in item for item in operations.values()):
        return {'status': 'failed', 'operations': operations, 'ready': False}
    try:
        candidate = revision_state(context, native['candidate'], request(context, 'GET', native['candidate']))
    except Refused as error:
        if error.status == 404:
            return {'status': 'candidate-unavailable', 'operations': operations, 'ready': False}
        raise
    uri = service.get('uri', '')
    url = uri if isinstance(uri, str) and re.fullmatch(r'https://[A-Za-z0-9.-]+/?', uri) else ''
    result = {'url': url, 'revision': candidate['revision'], 'image': candidate['image'], 'revision_uid': candidate['uid'],
              'previous_revision': native['baseline']['route'], 'no_op': native['no_op'], 'ready': candidate['ready'],
              'operations': operations}
    if value.get('candidate_uid') and candidate['uid'] != value['candidate_uid']:
        return {**result, 'status': 'candidate-replaced'}
    if candidate['image'] != native['image']:
        return {**result, 'status': 'image-mismatch'}
    if candidate['failed']:
        return {**result, 'status': 'failed'}
    if not candidate['ready'] or not ready(service) or any(not item['done'] for item in operations.values()):
        return {**result, 'status': 'pending'}
    if template_digest(service) != native['template_digest']:
        return {**result, 'status': 'configuration-changed'}
    if not native['no_op'] and revision(context, service.get('latestCreatedRevision')) != native['candidate']:
        return {**result, 'status': 'candidate-superseded'}
    actual = route(context, service, True)
    desired = route(context, service)
    result.update(route=actual, desired_route=desired)
    if actual != desired:
        return {**result, 'status': 'routing-pending'}
    if actual == native['candidate']:
        admitted = value['status'] in {'traffic-submitted', 'traffic-acknowledged', 'converged', 'no-op'}
        return {**result, 'status': 'succeeded' if admitted else 'candidate-serving-without-recorded-switch'}
    if actual == native['baseline']['route']:
        return {**result, 'status': 'baseline-serving'}
    return {**result, 'status': 'routing-changed'}


# Persist a safe observation while leaving the recorded write phase and controller outcome intact
def inspect(context, prepared=None):
    value = load(context, prepared)
    result = observe(context, value)
    value['observed'], value['observed_at'] = result, int(time.time())
    record(context, value)
    explanations = {
        'succeeded': 'The exact candidate image and route are observed. This read does not change the release or controller outcome.',
        'baseline-serving': 'The requested candidate is ready; the baseline still receives all traffic. Inspect this attempt before preparing a new change.',
        'pending': 'The provider has not confirmed the candidate and native operations complete. Inspect again without repeating a write.',
        'routing-pending': 'Desired and observed traffic differ. Wait for provider readback; no traffic update was repeated.',
        'failed': 'A native operation or the candidate failed. Inspect the retained attempt and current target before a new release.',
        'candidate-unavailable': 'The intended candidate is unavailable. Submission may be uncertain; no write was repeated.',
        'service-unavailable': 'The selected service is unavailable. Retained native operation facts do not prove current service health.',
        'image-mismatch': 'The actual candidate image differs from the reviewed image. Do not switch traffic or reuse this attempt.',
        'target-changed': 'The physical service identity changed. Prepare against the current owner instead of adopting it.',
        'candidate-replaced': 'The physical candidate revision changed. Keep its history and inspect the current owner.',
        'configuration-changed': 'Preserved service configuration changed. Review the current target before preparing another attempt.',
        'candidate-superseded': 'A newer candidate superseded this attempt. Inspect current routing before any new write.',
        'routing-changed': 'Traffic points to a different revision. Inspect the external change before another attempt.',
        'candidate-serving-without-recorded-switch': 'The candidate receives traffic without this attempt recording a switch. Inspect that external outcome separately.',
    }
    return {**result, 'operation': value['status'], 'replay_allowed': False, 'reason': explanations[result['status']]}



# Admit exactly one candidate write and one conditional traffic switch for this reviewed attempt
def apply(context, prepared):
    native = reviewed(context, prepared)
    path = context.stage.parent / JOURNAL
    if path.exists() or path.is_symlink():
        raise Refused('A Cloud Run operation was already attempted; inspect it without replaying', 409)
    service = request(context, 'GET', native['scope']['service'])
    if snapshot(context, service) != native['baseline']:
        raise Refused('Cloud Run changed after preparation; prepare a fresh release', 409)
    value = {'request': native, 'request_digest': prepared['request_digest'],
             'status': 'no-op' if native['no_op'] else 'candidate-submitted'}
    if native['no_op']:
        record(context, value)
        observed = inspect(context, prepared)
        if observed['status'] != 'succeeded':
            raise Refused('Cloud Run no-op could not verify the exact serving image; inspect without replaying', 409)
        return {key: observed[key] for key in ('revision', 'image', 'previous_revision', 'no_op', 'url')}
    try:
        request(context, 'GET', native['candidate'])
    except Refused as error:
        if error.status != 404:
            raise
    else:
        raise Refused('The intended Cloud Run candidate already exists; inspect it instead of adopting or overwriting it', 409)
    containers = copy.deepcopy(service['template']['containers'])
    containers[0]['image'] = native['image']
    expected = copy.deepcopy(service)
    expected['template']['containers'] = containers
    expected_template = template_digest(expected)
    pinned = [{'type': REVISION, 'revision': native['baseline']['route'].rsplit('/', 1)[1], 'percent': 100}]
    record(context, value)
    response = request(context, 'PATCH', native['scope']['service'],
        {'name': native['scope']['service'], 'etag': native['baseline']['etag'],
         'template': {'revision': native['candidate'].rsplit('/', 1)[1], 'containers': containers}, 'traffic': pinned},
        'template.revision,template.containers,traffic')
    value.update(status='candidate-acknowledged', candidate_operation=operation(context, response)['name'])
    record(context, value)
    deadline = time.monotonic() + min(240, context.component.timeout_seconds)
    wait(context, value['candidate_operation'], deadline)
    current = request(context, 'GET', native['scope']['service'])
    current_snapshot = snapshot(context, current)
    # latestReadyRevision may still name the serving baseline while the candidate has no traffic
    candidate = revision_state(context, native['candidate'], request(context, 'GET', native['candidate']))
    if (current_snapshot['uid'] != native['baseline']['uid'] or current_snapshot['route'] != native['baseline']['route']
            or current_snapshot['latest_created'] != native['candidate']
            or current_snapshot['template_digest'] != expected_template
            or candidate['image'] != native['image'] or not candidate['ready']):
        raise Refused('Cloud Run candidate, preserved configuration or baseline route changed; no traffic write was admitted', 409)
    value.update(status='candidate-ready', candidate_uid=candidate['uid'])
    record(context, value)
    value['status'] = 'traffic-submitted'
    record(context, value)
    response = request(context, 'PATCH', native['scope']['service'],
        {'name': native['scope']['service'], 'etag': current_snapshot['etag'],
         'traffic': [{'type': REVISION, 'revision': native['candidate'].rsplit('/', 1)[1], 'percent': 100}]}, 'traffic')
    value.update(status='traffic-acknowledged', traffic_operation=operation(context, response)['name'])
    record(context, value)
    wait(context, value['traffic_operation'], deadline)
    observed = inspect(context, prepared)
    if observed['status'] != 'succeeded':
        raise Refused('Cloud Run did not confirm the exact image and traffic outcome; inspect without replaying', 409)
    value = load(context, prepared)
    value['status'] = 'converged'
    record(context, value)
    return {key: observed[key] for key in ('revision', 'image', 'previous_revision', 'no_op', 'url')}
