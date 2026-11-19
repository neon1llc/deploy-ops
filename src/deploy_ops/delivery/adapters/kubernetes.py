# src/deploy_ops/delivery/adapters/kubernetes.py - Pin a native connection snapshot and one existing Deployment through review and readback.


import base64
import hashlib
import json
import os
import time
from urllib.parse import urlparse

from ...config import fingerprint
from ...errors import Refused
from ..files import read_regular
from .base import need


# Bind public connection facts while keeping the flattened credential snapshot only in memory
class Session:
    # Read one flattened native selection without publishing its user credentials
    def __init__(self, context):
        if os.name != 'posix':
            raise Refused('Pinned Kubernetes sessions currently require a POSIX stdin device', 422)
        self.context = context
        selected = need(context.options, 'context')
        try:
            version = json.loads(context.command(['kubectl', 'version', '--client', '--output=json'], capture=True))
        except ValueError as error:
            raise Refused('Kubernetes client returned unsupported version metadata', 422) from error
        raw = context.command(['kubectl', '--context', selected, 'config', 'view', '--minify', '--flatten',
                               '--raw', '--output=json'], capture=True)
        try:
            value = json.loads(raw)
            if value.get('current-context') != selected or len(value['contexts']) != 1 or len(value['clusters']) != 1:
                raise ValueError('Ambiguous selected connection')
            entry, cluster = value['contexts'][0], value['clusters'][0]
            if entry['name'] != selected or entry['context']['cluster'] != cluster['name']:
                raise ValueError('Selected cluster differs')
            settings = cluster['cluster']
            server = settings['server']
            parsed = urlparse(server)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or settings.get('insecure-skip-tls-verify')):
                raise ValueError('Unsupported transport')
            ca = base64.b64decode(settings.get('certificate-authority-data', ''), validate=True)
            if settings.get('certificate-authority'):
                raise ValueError('Unflattened trust path')
            if len(ca) > 1_000_000:
                raise ValueError('Trust material exceeds bound')
            self.binding = {'version': 1, 'context': selected, 'cluster': cluster['name'], 'server': server,
                            'namespace': need(context.options, 'namespace'), 'deployment': need(context.options, 'deployment'),
                            'container': need(context.options, 'container'),
                            'tls_server_name': settings.get('tls-server-name') or '',
                            'ca_sha256': hashlib.sha256(ca).hexdigest() if ca else None,
                            'trust': 'explicit-ca' if ca else 'native-system-roots',
                            'proxy_digest': fingerprint(settings.get('proxy-url')),
                            'user_label': entry['context'].get('user', ''),
                            'client_version': version['clientVersion']['gitVersion']}
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            raise Refused('Kubernetes needs one selected HTTPS connection with native TLS verification enabled; no raw configuration was retained', 422) from error
        # Native reads and writes use this same snapshot, not a mutable context alias on disk
        self.snapshot = json.dumps(value)

    # Pass credentials through the existing private anonymous stdin stream, never argv or release files
    def call(self, *arguments):
        return self.context.command(['kubectl', '--kubeconfig=/dev/stdin', '--context', self.binding['context'],
                                     '--namespace', self.binding['namespace'], *arguments],
                                    capture=True, input_text=self.snapshot)

    # Select the existing Deployment without importing its full spec or secret-bearing values into evidence
    def deployment(self):
        return json.loads(self.call('get', 'deployment/' + self.binding['deployment'], '-o', 'json'))


# Validate native object identity and retain only the selected container baseline
def baseline(session, value):
    try:
        metadata = value['metadata']
        if (value['apiVersion'] != 'apps/v1' or value['kind'] != 'Deployment'
                or metadata['name'] != session.binding['deployment']
                or metadata['namespace'] != session.binding['namespace'] or metadata.get('deletionTimestamp')):
            raise ValueError('Wrong or terminating object')
        uid, revision = metadata['uid'], metadata['resourceVersion']
        if any(not isinstance(item, str) or not 0 < len(item) <= 256 for item in (uid, revision)):
            raise ValueError('Missing object identity')
        containers = value['spec']['template']['spec']['containers']
        indices = [index for index, item in enumerate(containers) if item['name'] == session.binding['container']]
        if len(indices) != 1 or not isinstance(containers[indices[0]]['image'], str):
            raise ValueError('Ambiguous container')
        return {'uid': uid, 'resource_version': revision, 'container_index': indices[0],
                'previous_image': containers[indices[0]]['image']}
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise Refused('Kubernetes needs one nonterminating existing Deployment with exact UID and selected container', 422) from error


# Prepare the connection and object identity without changing the namespace or workload
def prepare(context, image):
    session = Session(context)
    current = baseline(session, session.deployment())
    request = {'version': 1, 'connection': session.binding, 'baseline': current, 'image': image,
               'no_op': current['previous_image'] == image}
    return {'kubernetes_request': request, 'request_digest': fingerprint(request)}


# Refuse old mutation evidence before credentials, claim or native access
def reviewed(prepared):
    request = prepared.get('kubernetes_request', {})
    if (type(request.get('version')) is not int or request.get('version') != 1 or not request.get('connection') or not request.get('baseline')
            or request.get('image') != prepared.get('image') or fingerprint(request) != prepared.get('request_digest')):
        raise Refused('Legacy Kubernetes approval lacks its connection and object UID. Preserve history and prepare fresh evidence', 409)
    return request


# Persist one public intent/outcome record through the existing durable native journal writer
def record(context, value):
    from .cloud import durable_record
    durable_record(context.stage.parent / 'kubernetes-operation.json', value)


# Keep controller completion separate from current UID/image/generation readback
def observation(session, request):
    value = session.deployment()
    current = baseline(session, value)
    status = value.get('status', {})
    generation = value['metadata'].get('generation')
    observed = status.get('observedGeneration')
    ready = (type(generation) is int and type(observed) is int and observed >= generation
             and status.get('updatedReplicas', 0) == value['spec'].get('replicas', 1)
             and status.get('availableReplicas', 0) == value['spec'].get('replicas', 1))
    return {'uid': current['uid'], 'image': current['previous_image'], 'generation': generation,
            'observed_generation': observed, 'ready': ready,
            'status': 'object-replaced' if current['uid'] != request['baseline']['uid'] else
                      'image-mismatch' if current['previous_image'] != request['image'] else
                      'converged' if ready else 'pending'}


# Submit one UID-tested patch on the pinned connection, then observe without replay
def apply(context, prepared):
    request = reviewed(prepared)
    path = context.stage.parent / 'kubernetes-operation.json'
    if path.exists() or path.is_symlink():
        raise Refused('A Kubernetes operation was already attempted; inspect it without replaying', 409)
    session = Session(context)
    if session.binding != request['connection']:
        raise Refused('Kubernetes connection changed after review; no cluster write was sent', 409)
    current = baseline(session, session.deployment())
    if current != request['baseline']:
        raise Refused('Kubernetes Deployment UID, version or container changed after review; prepare fresh evidence', 409)
    journal = {'version': 1, 'request_digest': prepared['request_digest'], 'connection': session.binding,
               'uid': current['uid'], 'status': 'observing-no-op' if request['no_op'] else 'patch-submitted'}
    record(context, journal)
    deployment = 'deployment/' + session.binding['deployment']
    if not request['no_op']:
        index = current['container_index']
        patch = [
            {'op': 'test', 'path': '/metadata/uid', 'value': current['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': current['resource_version']},
            {'op': 'test', 'path': f'/spec/template/spec/containers/{index}/name', 'value': session.binding['container']},
            {'op': 'test', 'path': f'/spec/template/spec/containers/{index}/image', 'value': current['previous_image']},
            {'op': 'replace', 'path': f'/spec/template/spec/containers/{index}/image', 'value': request['image']},
        ]
        session.call('patch', deployment, '--type=json', '--patch', json.dumps(patch))
        journal['status'] = 'patch-acknowledged'
        record(context, journal)
    session.call('rollout', 'status', deployment, '--timeout=120s')
    actual = observation(session, request)
    journal.update(status=actual['status'], observation=actual, observed_at=int(time.time()))
    record(context, journal)
    if actual['status'] != 'converged':
        raise Refused('Kubernetes did not converge on the reviewed object UID and image; inspect without replay', 409)
    return {'deployment': deployment, 'image': request['image'], 'uid': current['uid'], 'no_op': request['no_op']}


# Read only the original connection and object; a changed alias never authorizes a different cluster read
def inspect(context, prepared):
    request = reviewed(prepared)
    path = context.stage.parent / 'kubernetes-operation.json'
    try:
        journal = json.loads(read_regular(path)) if path.exists() or path.is_symlink() else None
        if journal is not None and not isinstance(journal, dict):
            raise ValueError('Unsupported journal')
    except (OSError, ValueError) as error:
        raise Refused('Kubernetes operation journal is malformed; no native action was sent', 422) from error
    if journal and journal.get('request_digest') != prepared['request_digest']:
        raise Refused('Kubernetes journal does not match the reviewed operation', 409)
    session = Session(context)
    if session.binding != request['connection']:
        return {'status': 'connection-changed', 'replay_allowed': False, 'cluster_read_sent': False}
    return {'status': 'observed', 'connection': session.binding, 'observation': observation(session, request),
            'operation': journal['status'] if journal else 'not-submitted', 'replay_allowed': False}
