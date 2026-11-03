# tests/test_runtime_contracts.py - Exercise provider command contracts without claiming live cloud validation.

import json
from types import SimpleNamespace

import pytest

from deploy_ops.delivery.adapters import cloud, iac
from deploy_ops.delivery.adapters.base import Context
from deploy_ops.delivery.spec import Component
from deploy_ops.errors import Refused

IMAGE = "registry.example/app@sha256:" + "a" * 64


class Commands:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.heartbeat = lambda: None

    def run(self, argv, cwd, timeout, capture, input_text):
        self.calls.append(argv)
        reply = next(self.replies)
        return json.dumps(reply) if isinstance(reply, dict) else reply


def context(tmp_path, adapter, provider, options, replies):
    stage = tmp_path / "artifacts"
    stage.mkdir()
    return Context(Component(repository="app", kind="application", adapter=adapter), "app", "staging", provider,
                   tmp_path, stage, tmp_path / "deployed", options, Commands(replies), "release")


@pytest.mark.parametrize("adapter,provider,options,replies", [
    ("ecs", "aws", {"region": "us-east-1", "cluster": "cluster", "service": "app", "container": "web"}, [
        {"services": [{"taskDefinition": "old", "deploymentController": {"type": "ECS"}}]},
        {"taskDefinition": {"family": "app", "containerDefinitions": [{"name": "web", "image": "old"}]}},
        {"services": [{"taskDefinition": "old"}]}, {"taskDefinition": {"taskDefinitionArn": "new"}}, {}, "",
        {"services": [{"taskDefinition": "new", "deployments": [{"rolloutState": "COMPLETED"}]}]},
    ]),
    ("container-apps", "azure", {"name": "app", "resource_group": "apps", "subscription_id": "subscription"}, [
        {"properties": {"configuration": {"activeRevisionsMode": "Single"}, "latestReadyRevisionName": "old"}},
        {"properties": {"latestReadyRevisionName": "old"}}, {"properties": {"latestRevisionName": "new"}},
        {"properties": {"healthState": "Healthy", "provisioningState": "Provisioned"}},
    ]),
    ("cloud-run", "gcp", {"name": "app", "project_id": "project", "region": "us-central1"}, [
        {"status": {"latestReadyRevisionName": "old", "traffic": [{"revisionName": "old", "percent": 100}]}},
        {"status": {"latestReadyRevisionName": "old", "traffic": [{"revisionName": "old", "percent": 100}]}},
        {"status": {"latestCreatedRevisionName": "new", "latestReadyRevisionName": "new", "conditions": [{"type": "Ready", "status": "True"}]}},
        {"status": {"traffic": [{"revisionName": "new", "percent": 100}]}},
    ]),
    ("kubernetes", "local", {"context": "test", "namespace": "app", "deployment": "app", "container": "web"}, [
        {"metadata": {"resourceVersion": "123"}, "spec": {"template": {"spec": {"containers": [{"name": "web", "image": "old"}]}}}},
        "", "", {"spec": {"template": {"spec": {"containers": [{"name": "web", "image": IMAGE}]}}}},
    ]),
])
def test_prepare_apply_verify_contract(tmp_path, adapter, provider, options, replies):
    ctx = context(tmp_path, adapter, provider, {**options, "image": IMAGE}, replies)
    prepared = cloud.prepare(ctx)
    assert prepared["preview"]["changes"]
    result = cloud.apply(ctx, prepared)
    assert result["image"] == IMAGE
    assert ctx.process.calls


@pytest.mark.parametrize("image", ["app:latest", "app:v1", "--flag", "app@sha256:123"])
def test_mutable_or_malformed_images_refused(tmp_path, image):
    ctx = context(tmp_path, "ecs", "aws", {"image": image}, [])
    with pytest.raises(Refused):
        cloud.prepare(ctx)


def test_provider_rollout_never_writes_after_stale_revision(tmp_path):
    ctx = context(tmp_path, "ecs", "aws", {"region": "us-east-1", "cluster": "x", "service": "x"},
                  [{"services": [{"taskDefinition": "changed"}]}])
    with pytest.raises(Refused, match="changed"):
        cloud.apply(ctx, {"image": IMAGE, "previous_revision": "old"})
    assert len(ctx.process.calls) == 1


@pytest.mark.parametrize("adapter,binary", [("opentofu", "tofu"), ("terraform", "terraform")])
def test_saved_iac_plan_is_the_applied_artifact(tmp_path, adapter, binary):
    ctx = context(tmp_path, adapter, "local", {"local_state": True}, [
        "", "", "", {"format_version": "1.2", "planned_values": {}, "resource_changes": []}, {"terraform_version": "1.9.0"},
        {"terraform_version": "1.9.0"}, "", "", {},
    ])
    (tmp_path / ".terraform.lock.hcl").write_text("# Contract fixture only\n")
    prepared = iac.prepare(ctx)
    iac.apply(ctx, prepared)
    apply = next(call for call in ctx.process.calls if call[:2] == [binary, "apply"])
    assert apply[-1] == str(ctx.stage / "infrastructure.plan")
    assert "-auto-approve" not in apply
