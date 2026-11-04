# src/deploy_ops/delivery/adapters/cloud.py - Deliver immutable application images with provider-specific checks.


import copy
import hashlib
import json
import os
import re
import time

from ...client import file_digest, private_json
from ...errors import Refused
from .base import Context, need, preview

# Require immutable image references before any runtime update
IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
# Copy only fields accepted when registering a new ECS task revision
ECS_FIELDS = {
    "family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions",
    "volumes", "placementConstraints", "requiresCompatibilities", "cpu", "memory",
    "pidMode", "ipcMode", "proxyConfiguration", "inferenceAccelerators", "ephemeralStorage",
    "runtimePlatform", "enableFaultInjection",
}


# Pin every ECS request to the selected region
def aws(context: Context, *parts: str) -> list[str]:
    return ["aws", *parts, "--region", need(context.options, "region"), "--output", "json", "--no-cli-pager"]


# Bind recovery records to the component and exact runtime target
def ecs_scope(context: Context) -> dict:
    return {"release_id": context.release_id, "component": context.name,
            "environment": context.environment, "region": need(context.options, "region"),
            "cluster": need(context.options, "cluster"), "service": need(context.options, "service")}


# Flush write intent before a request whose response could be lost
def ecs_record(context: Context, record: dict) -> None:
    path = context.stage.parent / "ecs-operation.json"
    private_json(path, record)
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# Disable native retries because task registration has no idempotency token
def ecs_write(context: Context, *parts: str) -> str:
    process = copy.copy(context.process)
    process.env = {**getattr(process, "env", {}), "AWS_MAX_ATTEMPTS": "1", "AWS_RETRY_MODE": "standard"}
    return process.run(aws(context, "ecs", *parts), context.source,
                       context.component.timeout_seconds, True, None)


# Keep provider messages and task environment values out of readback events
def ecs_observation(service: dict) -> dict:
    return {"revision": service.get("taskDefinition"), "status": service.get("status"),
            "desired_count": service.get("desiredCount"), "running_count": service.get("runningCount"),
            "pending_count": service.get("pendingCount"),
            "deployments": [{key: item.get(key) for key in
                ("id", "status", "taskDefinition", "rolloutState", "desiredCount", "runningCount", "pendingCount")}
                for item in service.get("deployments", [])]}


# Observe accepted or uncertain ECS requests without resubmitting them
def inspect_pending(context: Context) -> dict:
    if context.component.adapter != "ecs":
        raise Refused("This application adapter has no operation readback contract", 422)
    path = context.stage.parent / "ecs-operation.json"
    if not path.is_file():
        raise Refused("No ECS operation has been submitted for this release", 409)
    record = json.loads(path.read_text())
    if record.get("scope") != ecs_scope(context):
        raise Refused("ECS operation target differs from its recorded scope", 409)

    # Search only one bounded page and require the release marker for every candidate
    candidates = []
    truncated = False
    if record.get("revision"):
        candidates = [record["revision"]]
    else:
        listing = json.loads(context.command(aws(context, "ecs", "list-task-definitions", "--family-prefix",
            record["family"], "--sort", "DESC", "--max-items", "100"), capture=True))
        truncated = bool(listing.get("NextToken") or listing.get("nextToken"))
        for arn in listing.get("taskDefinitionArns", []):
            if arn.rsplit("/", 1)[-1].rsplit(":", 1)[0] != record["family"]:
                continue
            task = json.loads(context.command(aws(context, "ecs", "describe-task-definition",
                "--task-definition", arn, "--include", "TAGS"), capture=True))
            if any(tag.get("key") == "deploy-ops:operation" and tag.get("value") == record["operation_id"]
                   for tag in task.get("tags", [])):
                candidates.append(arn)

    # Discovery is evidence for cleanup and never permission to replay an uncertain registration
    current = json.loads(context.command(aws(context, "ecs", "describe-services", "--cluster",
        record["scope"]["cluster"], "--services", record["scope"]["service"]), capture=True))
    services = current.get("services", [])
    observed = ecs_observation(services[0]) if len(services) == 1 and not current.get("failures") else {"status": "missing"}
    record["observed"] = observed
    record["candidate_revisions"] = candidates
    record["discovery_truncated"] = truncated
    ecs_record(context, record)
    return {"operation": record["status"], "revision": record.get("revision"),
            "candidate_revisions": candidates, "discovery_truncated": truncated,
            "registration_uncertain": not bool(record.get("revision")), "service": observed,
            "replay_allowed": False}


# Require the same reviewed request before exposing operation recovery evidence
def inspect(context: Context, prepared: dict) -> dict:
    if context.component.adapter != "ecs":
        raise Refused("This application adapter has no operation readback contract", 422)
    path = context.stage.parent / "ecs-operation.json"
    if path.is_file() and json.loads(path.read_text()).get("request_digest") != prepared.get("request_digest"):
        raise Refused("ECS operation does not match the reviewed task request", 409)
    return inspect_pending(context)


# Bind Container Apps operations to one subscription and resource group
def azure(context: Context, *parts: str) -> list[str]:
    return ["az", "containerapp", *parts, "--name", need(context.options, "name"),
            "--resource-group", need(context.options, "resource_group"),
            "--subscription", need(context.options, "subscription_id"), "--output", "json"]


# Keep Cloud Run project and region explicit on every command
def google(context: Context, *parts: str) -> list[str]:
    return ["gcloud", "run", *parts, "--region", need(context.options, "region"),
            "--project", need(context.options, "project_id"), "--quiet", "--format=json"]


# Capture the current revision before reviewing an immutable image update
def prepare(context: Context) -> dict:
    image = need(context.options, "image")
    if not IMAGE.fullmatch(image):
        raise Refused("Application delivery requires an immutable image@sha256 digest", 422)
    adapter = context.component.adapter
    expected = {"ecs": "aws", "container-apps": "azure", "cloud-run": "gcp"}
    if adapter in expected and context.provider != expected[adapter]:
        raise Refused("Application adapter and cloud provider disagree", 422)
    result = {"image": image, "preview": preview(context.name, "application_revision", False)}
    if adapter == "ecs":
        current = json.loads(context.command(aws(context, "ecs", "describe-services", "--cluster",
            need(context.options, "cluster"), "--services", need(context.options, "service")), capture=True))
        if current.get("failures") or len(current.get("services", [])) != 1:
            raise Refused("ECS service must already exist. Deploy its infrastructure first")
        service = current["services"][0]
        if service.get("deploymentController", {}).get("type", "ECS") != "ECS":
            raise Refused("The ECS adapter supports the ECS deployment controller only", 422)
        response = json.loads(context.command(aws(context, "ecs", "describe-task-definition", "--task-definition",
            service["taskDefinition"], "--include", "TAGS"), capture=True))
        task = response["taskDefinition"]
        # Freeze the next task definition while preserving unrelated container settings
        definition = {key: value for key, value in task.items() if key in ECS_FIELDS}
        container = need(context.options, "container")
        matches = [item for item in definition["containerDefinitions"] if item["name"] == container]
        if len(matches) != 1:
            raise Refused("Expected exactly one named container in the ECS task")
        matches[0]["image"] = image

        # Preserve ordinary tags and identify this registration if its response disappears
        operation_id = hashlib.sha256(json.dumps({"scope": ecs_scope(context), "definition": definition},
            sort_keys=True).encode()).hexdigest()
        definition["tags"] = [tag for tag in response.get("tags", [])
            if not tag["key"].startswith("aws:") and tag["key"] != "deploy-ops:operation"]
        if len(definition["tags"]) >= 50:
            raise Refused("ECS recovery needs one available task-definition tag", 422)
        definition["tags"].append({"key": "deploy-ops:operation", "value": operation_id})
        private_json(context.stage / "task-definition.json", definition)
        result.update(previous_revision=service["taskDefinition"], family=definition["family"],
                      operation_id=operation_id, request_digest=file_digest(context.stage / "task-definition.json"))
    elif adapter == "container-apps":
        current = json.loads(context.command(azure(context, "show"), capture=True))
        if current["properties"].get("configuration", {}).get("activeRevisionsMode") != "Single":
            raise Refused("Use a reviewed external workflow for Azure multi-revision traffic management")
        result["previous_revision"] = current["properties"].get("latestReadyRevisionName")
    elif adapter == "cloud-run":
        current = json.loads(context.command(google(context, "services", "describe", need(context.options, "name")),
                                             capture=True))
        state = current.get("status", {})
        traffic = state.get("traffic", [])
        if len(traffic) != 1 or traffic[0].get("percent") != 100:
            raise Refused("Use a reviewed workflow for split-traffic Cloud Run services")
        result["previous_revision"] = state.get("latestReadyRevisionName")
        result["previous_traffic"] = traffic
    elif adapter == "kubernetes":
        for key in ("context", "namespace", "deployment", "container"):
            need(context.options, key)
        # Existing resources are owned by the infrastructure or GitOps repository
        base = ["kubectl", "--context", context.options["context"], "--namespace", context.options["namespace"]]
        current = json.loads(context.command([*base, "get", "deployment/" + context.options["deployment"], "-o", "json"], capture=True))
        containers = current["spec"]["template"]["spec"]["containers"]
        indices = [index for index, item in enumerate(containers) if item["name"] == context.options["container"]]
        if len(indices) != 1:
            raise Refused("Expected one named Kubernetes container")
        result["container_index"] = indices[0]
        result["previous_image"] = containers[indices[0]]["image"]
        result["resource_version"] = current["metadata"]["resourceVersion"]
        result["preview"] = preview(context.name, "kubernetes_image_update", False)
    return result


# Recheck the baseline and verify the provider's resulting revision
def apply(context: Context, prepared: dict) -> dict:
    adapter = context.component.adapter
    image = prepared["image"]
    if adapter == "ecs":
        if (context.stage.parent / "ecs-operation.json").exists():
            raise Refused("An ECS write was already submitted or became uncertain; inspect it without replaying", 409)
        cluster, service = need(context.options, "cluster"), need(context.options, "service")
        current = json.loads(context.command(aws(context, "ecs", "describe-services", "--cluster", cluster,
                                                "--services", service), capture=True))
        if (current.get("failures") or len(current.get("services", [])) != 1
                or current["services"][0]["taskDefinition"] != prepared["previous_revision"]):
            raise Refused("ECS service changed after planning. Prepare a fresh release")
        if not prepared.get("request_digest") or file_digest(context.stage / "task-definition.json") != prepared["request_digest"]:
            raise Refused("ECS task request changed or lacks a recovery identity; prepare a new release", 409)

        # Persist exact request identity before the non-idempotent registration
        record = {"scope": ecs_scope(context), "status": "register-submitted", "family": prepared["family"],
                  "operation_id": prepared["operation_id"], "request_digest": prepared["request_digest"],
                  "previous_revision": prepared["previous_revision"], "image": image}
        ecs_record(context, record)
        task = json.loads(ecs_write(context, "register-task-definition", "--cli-input-json",
            "file://" + str(context.stage / "task-definition.json")))["taskDefinition"]["taskDefinitionArn"]
        record.update(status="registered", revision=task)
        ecs_record(context, record)

        # Retain the new ARN even when activation or its acknowledgement fails
        record["status"] = "update-submitted"
        ecs_record(context, record)
        ecs_write(context, "update-service", "--cluster", cluster, "--service", service, "--task-definition", task)
        record["status"] = "update-acknowledged"
        ecs_record(context, record)
        context.command(aws(context, "ecs", "wait", "services-stable", "--cluster", cluster, "--services", service))
        response = json.loads(context.command(aws(context, "ecs", "describe-services", "--cluster", cluster,
                                              "--services", service), capture=True))
        states = response.get("services", [])
        state = states[0] if len(states) == 1 and not response.get("failures") else {}
        record["observed"] = ecs_observation(state)
        converged = state.get("taskDefinition") == task and not any(
            deployment.get("rolloutState") == "FAILED" for deployment in state.get("deployments", []))
        record["status"] = "converged" if converged else "not-converged"
        ecs_record(context, record)
        if not converged:
            raise Refused("ECS did not converge to the requested revision. A rollback may have occurred")
        return {"revision": task, "image": image, "previous_revision": prepared["previous_revision"]}
    if adapter == "container-apps":
        before = json.loads(context.command(azure(context, "show"), capture=True))
        if before["properties"].get("latestReadyRevisionName") != prepared["previous_revision"]:
            raise Refused("Container App changed after planning. Prepare a fresh release")
        updated = json.loads(context.command(azure(context, "update", "--image", image), capture=True))
        revision = updated["properties"]["latestRevisionName"]
        # Poll the requested revision rather than assuming the update response proves health
        deadline = time.monotonic() + min(context.component.timeout_seconds, 240)
        while time.monotonic() < deadline:
            state = json.loads(context.command(azure(context, "revision", "show", "--revision", revision), capture=True))
            properties = state.get("properties", {})
            if properties.get("healthState") == "Healthy" and properties.get("provisioningState") == "Provisioned":
                return {"revision": revision, "image": image, "previous_revision": prepared.get("previous_revision", "")}
            if properties.get("provisioningState") in ("Failed", "Deprovisioned"):
                raise Refused("The requested Container Apps revision did not provision")
            context.process.heartbeat()
            time.sleep(2)
        raise Refused("The requested Container Apps revision did not become healthy")
    if adapter == "cloud-run":
        before = json.loads(context.command(google(context, "services", "describe", need(context.options, "name")), capture=True))
        if before.get("status", {}).get("latestReadyRevisionName") != prepared["previous_revision"] or before.get("status", {}).get("traffic") != prepared["previous_traffic"]:
            raise Refused("Cloud Run service changed after planning. Prepare a fresh release")
        service = json.loads(context.command(google(context, "deploy", need(context.options, "name"), "--image", image, "--no-traffic"),
                                             capture=True))
        state = service.get("status", {})
        ready = any(item.get("type") == "Ready" and item.get("status") == "True" for item in state.get("conditions", []))
        if not ready or not state.get("latestCreatedRevisionName") or state.get("latestReadyRevisionName") != state["latestCreatedRevisionName"]:
            raise Refused("Cloud Run did not confirm the requested revision is ready")
        # Move traffic only after the newly created revision is confirmed ready
        traffic = json.loads(context.command(google(context, "services", "update-traffic", need(context.options, "name"),
            "--to-revisions", state["latestReadyRevisionName"] + "=100"), capture=True))
        serving = traffic.get("status", {}).get("traffic", [])
        if not any(item.get("revisionName") == state["latestReadyRevisionName"] and item.get("percent") == 100 for item in serving):
            raise Refused("Cloud Run traffic did not converge to the requested revision")
        return {"revision": state["latestReadyRevisionName"], "image": image, "url": state.get("url", ""),
                "previous_revision": prepared.get("previous_revision", "")}
    base = ["kubectl", "--context", need(context.options, "context"), "--namespace", need(context.options, "namespace")]
    deployment = "deployment/" + need(context.options, "deployment")
    # Use atomic test operations to reject concurrent Kubernetes changes
    patch = [
        {"op": "test", "path": "/metadata/resourceVersion", "value": prepared["resource_version"]},
        {"op": "test", "path": f"/spec/template/spec/containers/{prepared['container_index']}/image", "value": prepared["previous_image"]},
        {"op": "replace", "path": f"/spec/template/spec/containers/{prepared['container_index']}/image", "value": image},
    ]
    context.command([*base, "patch", deployment, "--type=json", "--patch", json.dumps(patch)])
    context.command([*base, "rollout", "status", deployment, "--timeout=120s"])
    observed = json.loads(context.command([*base, "get", deployment, "-o", "json"], capture=True))
    matches = [container for container in observed["spec"]["template"]["spec"]["containers"]
               if container["name"] == context.options["container"]]
    if len(matches) != 1 or matches[0]["image"] != image:
        raise Refused("Kubernetes deployment no longer matches the requested image")
    return {"deployment": deployment, "image": image}
