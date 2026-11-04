# tests/test_runtime_contracts.py - Exercise provider command contracts without claiming live cloud validation.


import copy
import json
from types import SimpleNamespace

import pytest

from deploy_ops.delivery.adapters import cloud, iac
from deploy_ops.delivery.adapters.base import Context
from deploy_ops.delivery.spec import Component
from deploy_ops.errors import Refused

# Use one synthetic digest across all provider command fixtures
IMAGE = "registry.example/app@sha256:" + "a" * 64


# Capture command contracts without invoking a cloud provider
class Commands:

    # Keep ordered replies and calls local to each adapter case
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.heartbeat = lambda: None


    # Return the next provider-shaped response while recording its command
    def run(self, argv, cwd, timeout, capture, input_text):
        self.calls.append(argv)
        reply = next(self.replies)
        return json.dumps(reply) if isinstance(reply, dict) else reply


# Create an isolated artifact directory for each command-contract case
def context(tmp_path, adapter, provider, options, replies):
    stage = tmp_path / "artifacts"
    stage.mkdir()
    return Context(Component(repository="app", kind="application", adapter=adapter), "app", "staging", provider,
                   tmp_path, stage, tmp_path / "deployed", options, Commands(replies), "release")


# Each runtime must verify the immutable image's resulting revision
@pytest.mark.parametrize("adapter,provider,options,replies", [
    ("ecs", "aws", {"region": "us-east-1", "cluster": "cluster", "service": "app", "container": "web"}, [
        {"services": [{"taskDefinition": "old", "deploymentController": {"type": "ECS"}}]},
        {"taskDefinition": {"family": "app", "containerDefinitions": [{"name": "web", "image": "old"}]}},
        {"services": [{"taskDefinition": "old"}]}, {"taskDefinition": {"taskDefinitionArn": "new"}}, {}, "",
        {"services": [{"taskDefinition": "new", "deployments": [{"rolloutState": "COMPLETED"}]}]},
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


# Refuse mutable image references before making a provider call
@pytest.mark.parametrize("image", ["app:latest", "app:v1", "--flag", "app@sha256:123"])
def test_mutable_or_malformed_images_refused(tmp_path, image):
    ctx = context(tmp_path, "ecs", "aws", {"image": image}, [])
    with pytest.raises(Refused):
        cloud.prepare(ctx)


# Changed provider state must stop execution before the first write
def test_provider_rollout_never_writes_after_stale_revision(tmp_path):
    ctx = context(tmp_path, "ecs", "aws", {"region": "us-east-1", "cluster": "x", "service": "x"},
                  [{"services": [{"taskDefinition": "changed"}]}])
    with pytest.raises(Refused, match="changed"):
        cloud.apply(ctx, {"image": IMAGE, "previous_revision": "old"})
    assert len(ctx.process.calls) == 1


# Both IaC engines must apply the retained plan rather than replan
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


# Model exact app and revision resource identities without sending a native request
class ContainerCommands:
    # Preserve potentially sensitive provider fields only in this synthetic provider
    def __init__(self, scope):
        self.calls = []
        self.heartbeat = lambda: None
        self.lost_response = False
        self.current = {"id": scope["app_id"], "identity": {"type": "None"}, "properties": {
            "latestRevisionName": "app--old", "latestReadyRevisionName": "app--old",
            "configuration": {"activeRevisionsMode": "Single", "secrets": [{"value": "private-setting-sentinel"}]},
            "template": {"revisionSuffix": "old", "containers": [{"name": "web", "image": "registry.example/app@sha256:" + "b" * 64,
                "env": [{"name": "PRIVATE", "value": "private-env-sentinel"}]}]}}}
        self.revisions = {}
        self.revision()


    # Materialize the immutable current revision with independent health observations
    def revision(self):
        name = self.current["properties"]["latestRevisionName"]
        template = copy.deepcopy(self.current["properties"]["template"])
        if getattr(self, "native_shapes", False):
            template["revisionSuffix"] = None
            template["scale"].update(cooldownPeriod=None, pollingInterval=None)
            template["containers"][0]["resources"].pop("ephemeralStorage", None)
        self.revisions[name] = {"id": self.current["id"] + "/revisions/" + name, "name": name, "properties": {
            "template": template, "active": True,
            "healthState": "Healthy", "provisioningState": "Provisioned"}}


    # Simulate accepted writes including loss of their acknowledgement
    def run(self, argv, cwd, timeout, capture, input_text):
        self.calls.append(argv)
        assert capture and argv[:2] == ["az", "containerapp"]
        if argv[2] == "show":
            return json.dumps(self.current)
        if argv[2:4] == ["revision", "show"]:
            return json.dumps(self.revisions[argv[argv.index("--revision") + 1]])
        assert argv[2] == "update"
        assert argv[argv.index("--container-name") + 1] == "web"
        properties = self.current["properties"]
        suffix = argv[argv.index("--revision-suffix") + 1]
        properties["template"]["revisionSuffix"] = suffix
        properties["template"]["containers"][0]["image"] = argv[argv.index("--image") + 1]
        properties["latestRevisionName"] = properties["latestReadyRevisionName"] = "app--" + suffix
        self.revision()
        if self.lost_response:
            raise Refused("Synthetic acknowledgement loss")
        return json.dumps(self.current)


# Share one realistic Single-mode app across the bounded recovery cases
@pytest.fixture
def container_app(tmp_path):
    ctx = context(tmp_path, "container-apps", "azure", {"name": "app", "resource_group": "apps",
        "subscription_id": "11111111-1111-1111-1111-111111111111", "image": IMAGE}, [])
    ctx.process = ContainerCommands(cloud.container_scope(ctx))
    return ctx


# Durable intent binds the exact image and revision before a response can disappear
def test_container_apps_lost_response_inspection_never_replays(container_app):
    ctx = container_app
    prepared = cloud.prepare(ctx)
    ctx.process.lost_response = True
    with pytest.raises(Refused, match="acknowledgement loss"):
        cloud.apply(ctx, prepared)
    observed = cloud.inspect(ctx, prepared)
    assert observed["status"] == "succeeded" and observed["replay_allowed"] is False
    assert observed["revision"] == prepared["azure_request"]["revision"]
    assert cloud.inspect_pending(ctx)["status"] == "succeeded"
    with pytest.raises(Refused, match="without replaying"):
        cloud.apply(ctx, prepared)
    assert sum(call[2] == "update" for call in ctx.process.calls) == 1
    record = (ctx.stage.parent / "container-apps-operation.json").read_text()
    assert "private-setting-sentinel" not in record and "private-env-sentinel" not in record
    assert "private-" not in json.dumps([prepared, observed])


# Same-image delivery verifies the existing healthy revision without creating another
@pytest.mark.parametrize("same_image", [False, True])
def test_container_apps_update_and_true_no_op(container_app, same_image):
    ctx = container_app
    if same_image:
        ctx.options["image"] = ctx.process.current["properties"]["template"]["containers"][0]["image"]
    prepared = cloud.prepare(ctx)
    assert prepared["preview"]["changes"][0]["action"] == ("no-op" if same_image else "update")
    output = cloud.apply(ctx, prepared)
    assert output["no_op"] is same_image
    assert output["image"] == ctx.options["image"]
    assert sum(call[2] == "update" for call in ctx.process.calls) == (0 if same_image else 1)
    if same_image:
        assert output["revision"] == output["previous_revision"] == "app--old"


# Reproduce sanitized native app and revision shapes instead of mirroring the implementation
@pytest.mark.parametrize("same_image", [False, True])
def test_container_apps_native_default_representations_converge(container_app, same_image):
    ctx = container_app
    template = ctx.process.current["properties"]["template"]
    template.update(revisionSuffix="", initContainers=None, serviceBinds=None, volumes=None,
                    terminationGracePeriodSeconds=None,
                    scale={"minReplicas": 1, "maxReplicas": 1, "cooldownPeriod": 300, "pollingInterval": 30, "rules": None})
    template["containers"][0].update(resources={"cpu": 0.25, "memory": "0.5Gi", "ephemeralStorage": "1Gi"},
                                    command=["python", "-c"], args=["synthetic app"], probes=[{
                                        "type": "Readiness", "httpGet": {"path": "/health", "port": 8080, "scheme": "HTTP"},
                                        "initialDelaySeconds": 3, "periodSeconds": 5, "timeoutSeconds": 2, "failureThreshold": 12}])
    ctx.process.native_shapes = True
    ctx.process.revision()
    if same_image:
        ctx.options["image"] = template["containers"][0]["image"]
    prepared = cloud.prepare(ctx)
    result = cloud.apply(ctx, prepared)
    assert result["no_op"] is same_image
    assert cloud.inspect(ctx, prepared)["status"] == "succeeded"
    assert sum(call[2] == "update" for call in ctx.process.calls) == (0 if same_image else 1)


# Default normalization never hides custom resource limits, scaling or executable configuration
@pytest.mark.parametrize("field", ["cooldown", "polling", "storage", "cpu", "memory", "env", "command", "probes", "future"])
def test_container_template_preserves_nondefault_changes(field):
    app = {"revisionSuffix": "", "scale": {"cooldownPeriod": 300, "pollingInterval": 30, "minReplicas": 1},
           "containers": [{"name": "web", "image": IMAGE, "resources": {
               "cpu": 0.25, "memory": "0.5Gi", "ephemeralStorage": "1Gi"},
               "env": [{"name": "PRIVATE", "value": "synthetic"}], "command": ["server"],
               "probes": [{"type": "Readiness", "httpGet": {"path": "/health", "port": 8080}}]}]}
    revision = copy.deepcopy(app)
    revision["revisionSuffix"] = None
    revision["scale"].update(cooldownPeriod=None, pollingInterval=None)
    revision["containers"][0]["resources"].pop("ephemeralStorage")
    assert cloud.container_template(app) == cloud.container_template(revision)
    if field in {"cooldown", "polling"}:
        app["scale"]["cooldownPeriod" if field == "cooldown" else "pollingInterval"] = 600
    elif field in {"storage", "cpu", "memory"}:
        key, value = {"storage": ("ephemeralStorage", "2Gi"), "cpu": ("cpu", 0.5), "memory": ("memory", "1Gi")}[field]
        app["containers"][0]["resources"][key] = value
    elif field == "env":
        app["containers"][0]["env"][0]["value"] = "different"
    elif field == "command":
        app["containers"][0]["command"] = ["another-server"]
    elif field == "probes":
        app["containers"][0]["probes"][0]["httpGet"]["path"] = "/other"
    else:
        app["newProviderSetting"] = "not-a-known-default"
    assert cloud.container_template(app) != cloud.container_template(revision)


# Changes beyond the ready revision also invalidate the reviewed baseline before writes
@pytest.mark.parametrize("change", ["template", "configuration", "identity", "pending", "mode", "scope"])
def test_container_apps_stale_baseline_stops_before_write(container_app, change):
    ctx = container_app
    prepared = cloud.prepare(ctx)
    current = ctx.process.current
    if change == "template":
        current["properties"]["template"]["containers"][0]["env"][0]["value"] = "changed"
    elif change == "configuration":
        current["properties"]["configuration"]["secrets"] = []
    elif change == "identity":
        current["identity"] = {"type": "SystemAssigned"}
    elif change == "pending":
        current["properties"]["latestRevisionName"] = "app--pending"
    elif change == "mode":
        current["properties"]["configuration"]["activeRevisionsMode"] = "Multiple"
    else:
        current["id"] += "-other"
    with pytest.raises(Refused):
        cloud.apply(ctx, prepared)
    assert not any(call[2] == "update" for call in ctx.process.calls)
    assert not (ctx.stage.parent / "container-apps-operation.json").exists()


# Readback never adopts another resource or a revision with changed environment settings
@pytest.mark.parametrize("change", ["id", "name", "image", "template"])
def test_container_apps_rejects_wrong_revision_evidence(container_app, change):
    ctx = container_app
    prepared = cloud.prepare(ctx)
    cloud.apply(ctx, prepared)
    revision = ctx.process.revisions[prepared["azure_request"]["revision"]]
    if change in {"id", "name"}:
        revision[change] += "-other"
    elif change == "image":
        revision["properties"]["template"]["containers"][0]["image"] = "wrong"
    else:
        revision["properties"]["template"]["containers"][0]["env"] = []
    with pytest.raises(Refused):
        cloud.inspect(ctx, prepared)
    assert sum(call[2] == "update" for call in ctx.process.calls) == 1


# Missing intent and altered reviewed requests cannot authorize adoption or another write
def test_container_apps_recovery_requires_matching_intent(container_app):
    ctx = container_app
    prepared = cloud.prepare(ctx)
    with pytest.raises(Refused, match="No owned"):
        cloud.inspect_pending(ctx)
    cloud.apply(ctx, prepared)
    altered = copy.deepcopy(prepared)
    altered["azure_request"]["image"] = "wrong"
    with pytest.raises(Refused, match="reviewed request"):
        cloud.inspect(ctx, altered)


# An unavailable revision is uncertain evidence rather than permission for another update
def test_container_apps_unavailable_revision_remains_unknown(container_app, monkeypatch):
    ctx = container_app
    prepared = cloud.prepare(ctx)
    cloud.apply(ctx, prepared)
    native = ctx.process.run

    # Preserve app readback while simulating an ambiguous revision lookup failure
    def unavailable(argv, *args):
        if argv[2:4] == ["revision", "show"]:
            raise Refused("Synthetic unavailable revision")
        return native(argv, *args)

    monkeypatch.setattr(ctx.process, "run", unavailable)
    result = cloud.inspect(ctx, prepared)
    assert result["status"] == "unknown" and result["replay_allowed"] is False
    assert sum(call[2] == "update" for call in ctx.process.calls) == 1


# Avoid an implicit first-container choice in an existing multi-container app
def test_container_apps_multiple_containers_require_another_workflow(container_app):
    ctx = container_app
    ctx.process.current["properties"]["template"]["containers"].append({"name": "sidecar", "image": IMAGE})
    with pytest.raises(Refused, match="exactly one"):
        cloud.prepare(ctx)
    assert not any(call[2] == "update" for call in ctx.process.calls)


# Health of a different revision cannot prove the requested app is serving
@pytest.mark.parametrize("change,expected", [("inactive", "unknown"), ("pending", "unknown"), ("failed", "failed")])
def test_container_apps_readback_reports_unconverged_state(container_app, change, expected):
    ctx = container_app
    prepared = cloud.prepare(ctx)
    cloud.apply(ctx, prepared)
    properties = ctx.process.revisions[prepared["azure_request"]["revision"]]["properties"]
    if change == "inactive":
        properties["active"] = False
    elif change == "pending":
        ctx.process.current["properties"]["latestReadyRevisionName"] = "app--old"
    else:
        properties["provisioningState"] = "Failed"
    assert cloud.inspect(ctx, prepared)["status"] == expected
