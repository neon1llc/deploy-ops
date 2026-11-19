# tests/cloud_run_fixture.py - Model native v2 acceptance separately from acknowledgement and readback.


import copy
from deploy_ops.delivery.adapters import cloud_run
from deploy_ops.errors import Refused


# Retain one service and immutable revisions while allowing exact boundary failure injection
class CloudRunFixture:
    def __init__(self):
        self.services = {}
        self.revisions = {}
        self.calls = []
        self.after_write = None
        self.before_call = None


    # Supply only explicit provider facts; environment data must never reach public records
    def service(self, context):
        scope = cloud_run.scope(context)
        name = scope['service']
        if name not in self.services:
            old = name + '/revisions/' + scope['name'] + '-old'
            self.services[name] = {'name': name, 'uid': 'service-physical-uid', 'etag': 'etag-1',
                'generation': '1', 'observedGeneration': '1',
                'terminalCondition': {'state': 'CONDITION_SUCCEEDED'}, 'latestCreatedRevision': old,
                'latestReadyRevision': old, 'template': {'revision': old.rsplit('/', 1)[1],
                    'containers': [{'name': 'web', 'image': 'registry.example/app@sha256:' + 'b' * 64,
                                    'env': [{'name': 'PRIVATE', 'value': 'PRIVATE-TEMPLATE-SENTINEL'}]}]},
                'traffic': [{'type': cloud_run.REVISION, 'revision': old.rsplit('/', 1)[1], 'percent': 100}],
                'trafficStatuses': [{'type': cloud_run.REVISION, 'revision': old.rsplit('/', 1)[1], 'percent': 100}]}
            self.revisions[old] = self.revision(name, old, self.services[name]['template']['containers'])
        return self.services[name]


    # A revision's actual image and readiness are independently observable
    @staticmethod
    def revision(service, name, containers):
        return {'name': name, 'service': service, 'uid': name.rsplit('/', 1)[1] + '-uid',
                'generation': '1', 'observedGeneration': '1',
                'conditions': [{'type': 'Ready', 'state': 'CONDITION_SUCCEEDED'}],
                'containers': copy.deepcopy(containers)}


    # Conditional native writes can succeed before their response is lost
    def request(self, context, method, name, body=None, mask=None):
        context.process.heartbeat()
        self.calls.append((method, name, copy.deepcopy(body), mask))
        service = self.service(context)
        if self.before_call:
            self.before_call(context, method, name, body, mask)
        if method == 'GET':
            if name == service['name']:
                return copy.deepcopy(service)
            if '/operations/' in name:
                return {'name': name, 'done': True, 'response': {'@type': 'Service'}}
            if name in self.revisions:
                return copy.deepcopy(self.revisions[name])
            raise Refused('not found', 404)
        assert method == 'PATCH'
        if body['etag'] != service['etag']:
            raise Refused('native etag conflict', 409)
        if mask == 'template.revision,template.containers,traffic':
            service['template'].update(copy.deepcopy(body['template']))
            candidate = service['name'] + '/revisions/' + body['template']['revision']
            service['latestCreatedRevision'] = candidate
            self.revisions[candidate] = self.revision(service['name'], candidate, body['template']['containers'])
        else:
            assert mask == 'traffic'
            service['latestReadyRevision'] = service['name'] + '/revisions/' + body['traffic'][0]['revision']
        service['traffic'] = copy.deepcopy(body['traffic'])
        service['trafficStatuses'] = copy.deepcopy(body['traffic'])
        service['generation'] = service['observedGeneration'] = str(int(service['generation']) + 1)
        service['etag'] = 'etag-' + service['generation']
        if self.after_write:
            self.after_write(context, mask)
        operation = cloud_run.scope(context)['service'].split('/services/')[0] + '/operations/attempt-' + service['generation']
        return {'name': operation, 'done': False}
