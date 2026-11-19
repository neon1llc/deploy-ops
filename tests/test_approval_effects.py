# tests/test_approval_effects.py - Keep migration and deletion authority tied to all native effects.

from copy import deepcopy

import pytest

from deploy_ops.config import fingerprint
from deploy_ops.errors import Refused
from deploy_ops.models import Claim, Decision
from deploy_ops.plans import normalize
from deploy_ops.policy import hard_denial
from deploy_ops.delivery.adapters import cloudformation as cfn


# Import/move annotation cannot erase an independent native update or replacement.
@pytest.mark.parametrize('engine', ['terraform', 'opentofu'])
@pytest.mark.parametrize('native,expected', [(['update'], 'update'), (['delete', 'create'], 'replace'),
                                            (['create', 'delete'], 'replace'), (['delete'], 'delete'),
                                            (['no-op'], None)])
def test_all_migration_effects_need_authority(system, proposal, source, engine, native, expected):
    controller, target, _ = system
    target.policy.allowed_actions = ['import', 'move', 'no-op']
    raw = deepcopy(proposal.preview)
    resource = raw['resource_changes'][0]
    resource['previous_address'] = 'aws_s3_bucket.old'
    resource['change'] = {'actions': native, 'importing': {'id': 'private-import-id'}}
    body = proposal.model_copy(update={'engine': engine, 'preview': raw})
    request = controller.submit(body, source)
    actions = [item['action'] for item in request['manifest']['preview']['changes']]
    assert actions == ([expected] if expected else []) + ['import', 'move']
    assert request['manifest']['native_effects_version'] == 2
    assert 'private-import-id' not in str(request['manifest'])
    assert request['status'] == ('denied' if expected else 'pending')
    if expected:
        assert expected in request['reason']
    else:
        approved = controller.decide(request['id'], Decision(digest=request['digest'], decision='approve'), 'human')
        assert approved['status'] == 'approved' and approved['execution'] is None


# Old request bytes/audit remain readable, but incomplete migration authority is not consumed.
def test_legacy_migration_claim_preserves_history(system, proposal, source):
    from deploy_ops.demo import sample_source
    controller, target, _ = system
    target.policy.allowed_actions += ['import']
    resource = proposal.preview['resource_changes'][0]
    resource['change']['importing'] = {'id': 'existing'}
    request = controller.submit(proposal, source)
    # Reproduce a previously stored approved manifest produced by the old normalizer.
    with controller.store.transaction() as connection:
        old = controller.store.request(connection, request['id'])
        old['manifest'].pop('native_effects_version')
        old['manifest']['preview']['changes'] = [
            {**old['manifest']['preview']['changes'][-1], 'action': 'import'}]
        old['digest'] = fingerprint(old['manifest'])
        controller.store.save_request(connection, old)
    approved = controller.decide(old['id'], Decision(digest=old['digest'], decision='approve'), 'human')
    before = deepcopy(controller.get(old['id']))
    audit = controller.store.audit()
    assert 'submit a fresh plan' in before['execution_blocker']
    with pytest.raises(Refused, match='Legacy migration'):
        controller.claim(old['id'], Claim(digest=old['digest'], artifact_digest=proposal.artifact_digest),
                         sample_source(target, 'execute'))
    assert controller.get(old['id']) == before
    assert controller.store.audit() == audit
    assert approved['status'] == 'approved'
    assert controller.decide(old['id'], Decision(digest=old['digest'], decision='reject'), 'human')['status'] == 'rejected'


# Malformed migration annotation and combined over-bound effects cannot become partial approval.
def test_migration_shape_and_combined_bound(proposal):
    for key, value in [('importing', {}), ('importing', False), ('previous_address', '')]:
        raw = deepcopy(proposal.preview)
        resource = raw['resource_changes'][0]
        (resource if key == 'previous_address' else resource['change'])[key] = value
        with pytest.raises(Refused, match='unsupported shape'):
            normalize('terraform', 'aws', raw)
    raw = deepcopy(proposal.preview)
    resource = raw['resource_changes'][0]
    resource['change']['importing'] = {'id': 'private'}
    raw['resource_changes'] = [deepcopy(resource) for _ in range(1001)]
    with pytest.raises(Refused, match='unsupported shape'):
        normalize('terraform', 'aws', raw)


# Native delete policy separates live retention, snapshot creation, original delete and stack removal.
def test_stack_snapshot_fates_and_policy(system):
    resources = {
        'Disk': {'Type': 'AWS::EC2::Volume', 'DeletionPolicy': 'Snapshot'},
        'Keep': {'Type': 'AWS::S3::Bucket', 'DeletionPolicy': 'Retain'},
        'KeepExcept': {'Type': 'AWS::S3::Bucket', 'DeletionPolicy': 'RetainExceptOnCreate'},
        'Database': {'Type': 'AWS::RDS::DBInstance'},
        'Cluster': {'Type': 'AWS::RDS::DBCluster'},
        'Member': {'Type': 'AWS::RDS::DBInstance', 'Properties': {'DBClusterIdentifier': 'cluster'}},
        'Drop': {'Type': 'AWS::S3::Bucket'},
    }
    inventory = [{'LogicalResourceId': key, 'ResourceType': value['Type']} for key, value in resources.items()]
    preview, retained, snapshots = cfn.deletion_effects('stack-id', resources, inventory)
    assert retained == ['Keep', 'KeepExcept']
    assert snapshots == ['Disk', 'Database', 'Cluster']
    actions = lambda name: [item['action'] for item in preview['changes'] if item['address'] == name]
    assert actions('stack-id') == ['delete']
    assert actions('Disk') == ['delete', 'snapshot']
    assert actions('Keep') == actions('KeepExcept') == ['read']
    assert actions('Database') == actions('Cluster') == ['delete', 'snapshot']
    assert actions('Member') == actions('Drop') == ['delete']
    target = system[1]
    target.policy.allowed_actions += ['delete']
    assert 'snapshot' in hard_denial(target, {'preview': preview})
    target.policy.allowed_actions += ['snapshot']
    assert hard_denial(target, {'preview': preview}) is None
    target.policy.blocked_types += ['AWS::CloudFormation::Stack']
    for values, items in [({}, []), ({'Keep': resources['Keep']}, [inventory[1]])]:
        empty, _, _ = cfn.deletion_effects('stack-id', values, items)
        assert 'outside' in hard_denial(target, {'preview': empty})


# Unsupported dynamic/default/nested cases refuse review instead of guessing native effects.
@pytest.mark.parametrize('resource,item', [
    ({'Type': 'AWS::S3::Bucket', 'DeletionPolicy': 'Snapshot'}, {'ResourceType': 'AWS::S3::Bucket'}),
    ({'Type': 'AWS::EC2::Volume', 'DeletionPolicy': {'Fn::If': ['x', 'Retain', 'Delete']}}, {'ResourceType': 'AWS::EC2::Volume'}),
    ({'Type': 'AWS::RDS::DBInstance', 'Properties': {'DBClusterIdentifier': {'Ref': 'MaybeAbsent'}}}, {'ResourceType': 'AWS::RDS::DBInstance'}),
    ({'Type': 'AWS::CloudFormation::Stack'}, {'ResourceType': 'AWS::CloudFormation::Stack'}),
    ({'Type': 'AWS::S3::Bucket'}, {'ResourceType': 'AWS::EC2::Volume'}),
    ({'Type': 'AWS::S3::Bucket'}, {'ResourceType': 'AWS::S3::Bucket', 'ResourceStatus': 'DELETE_SKIPPED'}),
])
def test_unsupported_deletion_fate_refuses(resource, item):
    with pytest.raises(Refused):
        cfn.deletion_effects('stack-id', {'Resource': resource}, [{'LogicalResourceId': 'Resource', **item}])


# The adapter rejects old mutation evidence before any native read/write or operation journal.
def test_legacy_destroy_refuses_before_native():
    class NoNative:
        def __getattr__(self, name):
            raise AssertionError('No native/context use permitted')
    with pytest.raises(Refused, match='Legacy stack deletion'):
        cfn.apply(NoNative(), {'operation': 'destroy', 'retained': ['old-snapshot']})


# Shared local-cloud scope requires stack and snapshot permissions and counts every effect row.
def test_snapshot_grant_does_not_widen_existing_authority():
    from deploy_ops.delivery.access import Grant, check_preview
    resource = {'Disk': {'Type': 'AWS::EC2::Volume', 'DeletionPolicy': 'Snapshot'}}
    inventory = [{'LogicalResourceId': 'Disk', 'ResourceType': 'AWS::EC2::Volume'}]
    preview, _, _ = cfn.deletion_effects('stack-id', resource, inventory)
    grant = Grant(provider='aws', identity={}, adapters=['cloudformation'],
                  resource_types=['AWS::EC2::Volume'], actions=['delete'], max_changes=10)
    before = grant.model_dump()
    with pytest.raises(Refused, match='outside'):
        check_preview(preview, grant)
    assert grant.model_dump() == before
    grant.resource_types.append('AWS::CloudFormation::Stack')
    with pytest.raises(Refused, match='outside'):
        check_preview(preview, grant)
    grant.actions.append('snapshot')
    grant.max_changes = 2
    with pytest.raises(Refused, match='change limit'):
        check_preview(preview, grant)
    grant.max_changes = 3
    check_preview(preview, grant)


# The application runner refuses old stack deletion before acquiring execution authority.
@pytest.mark.parametrize('adapter', ['cloudformation', 'cdk'])
def test_engine_legacy_destroy_refuses_before_claim(tmp_path, adapter):
    from types import SimpleNamespace
    from deploy_ops.delivery.engine import Engine
    release = {'id': 'old-release', 'nodes': {'infrastructure': {}}}
    envelope = {'adapter': adapter, 'prepared': {'operation': 'destroy'}}
    class NeverClaim:
        def claim(self, *args, **kwargs):
            raise AssertionError('Legacy approval must not be consumed')
    instance = SimpleNamespace(state=tmp_path, gate=NeverClaim(),
                               _reviewed_envelope=lambda *args: envelope)
    with pytest.raises(Refused, match='Legacy stack deletion'):
        Engine._apply(instance, release, 'infrastructure', {})
