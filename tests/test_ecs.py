# tests/test_ecs.py - Preserve ECS recovery evidence across ambiguous provider responses.


import json
from pathlib import Path

import pytest

from deploy_ops.delivery.adapters import cloud
from deploy_ops.delivery.adapters.base import Context
from deploy_ops.delivery.spec import Component
from deploy_ops.errors import Refused

# Keep provider state separate from copied command runners
IMAGE = "registry.example/app@sha256:" + "a" * 64
OLD = "arn:aws:ecs:us-east-1:123456789012:task-definition/app:1"
NEW = "arn:aws:ecs:us-east-1:123456789012:task-definition/app:2"


# Model accepted writes independently from their returned responses
class Commands:

    # Share observable provider state across the isolated mutation process copies
    def __init__(self, failure=None):
        self.env = {"AWS_MAX_ATTEMPTS": "9"}
        self.state = {"revision": OLD, "registered": {}, "calls": [], "failure": failure}


    # Return native-shaped responses while losing selected write acknowledgements
    def run(self, argv, cwd, timeout, capture, input_text):
        operation = argv[2]
        self.state["calls"].append((operation, dict(self.env)))
        if operation == "describe-services":
            result = {"services": [{"taskDefinition": self.state["revision"], "status": "ACTIVE",
                      "deployments": [{"rolloutState": "COMPLETED"}],
                      "events": [{"message": "private diagnostic"}]}]}
        elif operation == "describe-task-definition":
            arn = argv[argv.index("--task-definition") + 1]
            if arn == OLD:
                result = {"taskDefinition": {"family": "app", "containerDefinitions": [
                    {"name": "web", "image": "old", "environment": [{"name": "PRIVATE", "value": "hidden"}]}]},
                    "tags": [{"key": "owner", "value": "team"}, {"key": "aws:reserved", "value": "ignored"}]}
            else:
                result = self.state["registered"][arn]
        elif operation == "register-task-definition":
            assert self.env["AWS_MAX_ATTEMPTS"] == "1"
            request = json.loads(Path(argv[argv.index("--cli-input-json") + 1].removeprefix("file://")).read_text())
            self.state["registered"][NEW] = {"taskDefinition": {**request, "taskDefinitionArn": NEW}, "tags": request["tags"]}
            result = self.state["registered"][NEW]
        elif operation == "update-service":
            assert self.env["AWS_MAX_ATTEMPTS"] == "1"
            self.state["revision"] = NEW
            result = {}
        elif operation == "list-task-definitions":
            result = {"taskDefinitionArns": list(self.state["registered"])}
        elif operation == "wait":
            result = {}
        else:
            raise AssertionError(operation)

        # A transport failure can follow provider acceptance
        if operation == self.state["failure"]:
            raise Refused("Acknowledgement lost")
        return json.dumps(result)


# Build a frozen application request without using real credentials
def setup(tmp_path, failure=None):
    stage = tmp_path / "artifacts"
    stage.mkdir()
    process = Commands(failure)
    context = Context(Component(repository="app", kind="application", adapter="ecs"), "app", "test", "aws",
                      tmp_path, stage, tmp_path / "deployed",
                      {"region": "us-east-1", "cluster": "cluster", "service": "app", "container": "web", "image": IMAGE},
                      process, "release")
    return context, cloud.prepare(context)


# Readback finds the tagged revision even when registration returned no ARN
def test_lost_registration_response_cannot_create_another_revision(tmp_path):
    context, prepared = setup(tmp_path, "register-task-definition")
    frozen = (context.stage / "task-definition.json").read_bytes()
    with pytest.raises(Refused, match="Acknowledgement"):
        cloud.apply(context, prepared)
    record = json.loads((tmp_path / "ecs-operation.json").read_text())
    assert record["status"] == "register-submitted"
    assert "revision" not in record

    # Repeated apply is refused before any command, even after successful readback
    observed = cloud.inspect(context, prepared)
    assert observed["candidate_revisions"] == [NEW]
    assert observed["registration_uncertain"] and not observed["replay_allowed"]
    calls = len(context.process.state["calls"])
    with pytest.raises(Refused, match="without replaying"):
        cloud.apply(context, prepared)
    assert len(context.process.state["calls"]) == calls
    assert sum(name == "register-task-definition" for name, _ in context.process.state["calls"]) == 1
    assert (context.stage / "task-definition.json").read_bytes() == frozen


# Persist the exact new revision before any activation or stability failure
@pytest.mark.parametrize("failure,status", [("update-service", "update-submitted"), ("wait", "update-acknowledged")])
def test_activation_failures_keep_cleanup_identity(tmp_path, failure, status):
    context, prepared = setup(tmp_path, failure)
    with pytest.raises(Refused):
        cloud.apply(context, prepared)
    record = json.loads((tmp_path / "ecs-operation.json").read_text())
    assert record["revision"] == NEW and record["status"] == status
    observed = cloud.inspect(context, prepared)
    assert observed["service"]["revision"] == NEW
    assert "hidden" not in json.dumps(observed) and "private diagnostic" not in json.dumps(observed)
    with pytest.raises(Refused, match="without replaying"):
        cloud.apply(context, prepared)


# Successful delivery preserves tags and leaves an owner-only journal outside artifacts
def test_success_records_convergence_and_restores_process_environment(tmp_path):
    context, prepared = setup(tmp_path)
    result = cloud.apply(context, prepared)
    assert result["revision"] == NEW
    journal = tmp_path / "ecs-operation.json"
    assert journal.stat().st_mode & 0o777 == 0o600
    assert json.loads(journal.read_text())["status"] == "converged"
    assert context.process.env["AWS_MAX_ATTEMPTS"] == "9"
    tags = context.process.state["registered"][NEW]["tags"]
    assert {"key": "owner", "value": "team"} in tags
    assert not any(tag["key"].startswith("aws:") for tag in tags)
    assert list(context.stage.iterdir()) == [context.stage / "task-definition.json"]


# A changed frozen request must fail before registration intent or any provider write
def test_changed_request_is_refused(tmp_path):
    context, prepared = setup(tmp_path)
    (context.stage / "task-definition.json").write_text("{}")
    with pytest.raises(Refused, match="changed"):
        cloud.apply(context, prepared)
    assert not (tmp_path / "ecs-operation.json").exists()
    assert not any(name == "register-task-definition" for name, _ in context.process.state["calls"])


# Recovery cannot cross a component's approved runtime target
def test_readback_rejects_changed_scope_and_request(tmp_path):
    context, prepared = setup(tmp_path, "register-task-definition")
    with pytest.raises(Refused):
        cloud.apply(context, prepared)
    with pytest.raises(Refused, match="reviewed task"):
        cloud.inspect(context, {**prepared, "request_digest": "different"})
    context.options["service"] = "other"
    with pytest.raises(Refused, match="scope"):
        cloud.inspect(context, prepared)
