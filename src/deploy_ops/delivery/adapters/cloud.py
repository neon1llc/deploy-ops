# src/deploy_ops/delivery/adapters/cloud.py - Deliver immutable application images with provider-specific checks.


import copy
import hashlib
import json
import math
import os
import re
import time

from ...client import file_digest, private_json
from ...config import fingerprint
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
    durable_record(context.stage.parent / "ecs-operation.json", record)


# Sync operation intent before provider calls whose response may disappear
def durable_record(path, record: dict) -> None:
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
    if context.component.adapter == "container-apps":
        return container_inspect(context)
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
    if context.component.adapter == "container-apps":
        return container_inspect(context, prepared)
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


# Bind every observation to one public app identity and release
def container_scope(context: Context) -> dict:
    options = {key: need(context.options, key) for key in ("subscription_id", "resource_group", "name")}
    if (not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", options["subscription_id"])
            or not re.fullmatch(r"[A-Za-z0-9_().-]{1,90}", options["resource_group"])
            or options["resource_group"].endswith(".")
            or not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,30}[a-z0-9])?", options["name"])
            or "--" in options["name"]):
        raise Refused("Container Apps requires an explicit valid subscription, resource group and app name", 422)
    return {**options, "release_id": context.release_id, "component": context.name,
            "environment": context.environment,
            "app_id": f"/subscriptions/{options['subscription_id']}/resourceGroups/{options['resource_group']}/providers/Microsoft.App/containerApps/{options['name']}"}


# Normalize only documented defaults omitted by the native revision response
def container_template(template: dict) -> dict:
    value = copy.deepcopy(template)
    # Exact revision resource names bind suffix identity when the revision API omits this field
    value.pop("revisionSuffix", None)
    scale = value.get("scale")
    if isinstance(scale, dict):
        for key, default in (("cooldownPeriod", 300), ("pollingInterval", 30)):
            if scale.get(key) is None or (type(scale[key]) is int and scale[key] == default):
                scale.pop(key, None)

    # The documented replica CPU allowance explains only its default ephemeral storage
    containers = value.get("containers", [])
    if len(containers) == 1 and not value.get("initContainers"):
        resources = containers[0].get("resources", {})
        cpu = resources.get("cpu")
        if type(cpu) in (int, float) and math.isfinite(cpu) and cpu > 0:
            storage = "1Gi" if cpu <= 0.25 else "2Gi" if cpu <= 0.5 else "4Gi" if cpu <= 1 else "8Gi"
            if resources.get("ephemeralStorage") in (None, storage):
                resources.pop("ephemeralStorage", None)
    return value


# Hash sensitive app configuration while retaining only the selected public container identity
def container_snapshot(context: Context, current: dict) -> dict:
    scope = container_scope(context)
    properties = current.get("properties", {})
    if str(current.get("id", "")).lower() != scope["app_id"].lower():
        raise Refused("Container Apps returned an unexpected app identity")
    if properties.get("configuration", {}).get("activeRevisionsMode") != "Single":
        raise Refused("Use a reviewed external workflow for Azure multi-revision traffic management")
    containers = properties.get("template", {}).get("containers", [])
    if len(containers) != 1 or not containers[0].get("name") or not containers[0].get("image"):
        raise Refused("Container Apps delivery requires exactly one named application container", 422)
    bound = {key: properties.get(key) for key in ("template", "configuration", "environmentId", "managedEnvironmentId",
                                               "latestRevisionName", "latestReadyRevisionName", "workloadProfileName")}
    bound["identity"] = current.get("identity")
    return {"state_digest": fingerprint(bound), "template_digest": fingerprint(container_template(properties.get("template", {}))),
            "container": containers[0]["name"], "image": containers[0]["image"],
            "latest": properties.get("latestRevisionName"), "ready": properties.get("latestReadyRevisionName")}


# Derive a unique revision target before sending any native update
def container_request(context: Context, current: dict, image: str) -> dict:
    snapshot = container_snapshot(context, current)
    if (not isinstance(snapshot["ready"], str) or not snapshot["ready"].startswith(container_scope(context)["name"] + "--")
            or snapshot["ready"] != snapshot["latest"]):
        raise Refused("Container App has an unsettled revision; inspect it before preparing a release")
    scope = container_scope(context)
    suffix = "do-" + fingerprint({"scope": scope, "image": image, "baseline": snapshot["state_digest"]})[:20]
    template = copy.deepcopy(current["properties"]["template"])
    template["containers"][0]["image"] = image
    if snapshot["image"] != image:
        template["revisionSuffix"] = suffix
    return {"desired_template_digest": fingerprint(container_template(template)), "scope": scope,
            "previous_revision": snapshot["ready"], "previous_state_digest": snapshot["state_digest"],
            "container": snapshot["container"], "image": image, "suffix": suffix,
            "revision": snapshot["ready"] if snapshot["image"] == image else scope["name"] + "--" + suffix,
            "no_op": snapshot["image"] == image}


# Observe exact revision identity and activation without adopting unrelated provider state
def container_observe(context: Context, request: dict) -> dict:
    current = json.loads(context.command(azure(context, "show"), capture=True))
    snapshot = container_snapshot(context, current)
    revision = request["revision"]
    try:
        raw = json.loads(context.command(azure(context, "revision", "show", "--revision", revision), capture=True))
    except Refused:
        return {"status": "unknown", "revision": revision, "reason": "The named revision could not be read"}
    expected_id = request["scope"]["app_id"] + "/revisions/" + revision
    if str(raw.get("id", "")).lower() != expected_id.lower() or raw.get("name") != revision:
        raise Refused("Container Apps returned an unexpected revision identity")
    properties = raw.get("properties", {})
    containers = properties.get("template", {}).get("containers", [])
    image_matches = (len(containers) == 1 and containers[0].get("name") == request["container"]
                     and containers[0].get("image") == request["image"])
    if not image_matches or fingerprint(container_template(properties.get("template", {}))) != request["desired_template_digest"]:
        raise Refused("The named Container Apps revision does not match the reviewed template, image and container")
    provisioned = properties.get("provisioningState")
    failed = provisioned in {"Failed", "Deprovisioned"}
    converged = (snapshot["latest"] == revision and snapshot["ready"] == revision
                 and snapshot["template_digest"] == request["desired_template_digest"]
                 and snapshot["container"] == request["container"] and snapshot["image"] == request["image"]
                 and properties.get("active") is True and properties.get("healthState") == "Healthy"
                 and provisioned == "Provisioned")
    return {"status": "succeeded" if converged else "failed" if failed else "unknown", "revision": revision,
            "image": request["image"], "previous_revision": request["previous_revision"],
            "no_op": request["no_op"], "active": properties.get("active") is True}


# Reconcile only retained intent and never resubmit a potentially accepted update
def container_inspect(context: Context, prepared: dict | None = None) -> dict:
    path = context.stage.parent / "container-apps-operation.json"
    if not path.is_file() or path.is_symlink():
        raise Refused("No owned Container Apps operation record exists for this release", 409)
    record = json.loads(path.read_text())
    request = record.get("request", {})
    if (request.get("scope") != container_scope(context) or fingerprint(request) != record.get("request_digest")
            or (prepared is not None and (request != prepared.get("azure_request")
                                         or record["request_digest"] != prepared.get("request_digest")))):
        raise Refused("Container Apps operation differs from its reviewed request", 409)
    observed = container_observe(context, request)
    record["observed"] = observed
    durable_record(path, record)
    return {**observed, "operation": record["status"], "replay_allowed": False}


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
        request = container_request(context, current, image)
        result.update(azure_request=request, request_digest=fingerprint(request), previous_revision=request["previous_revision"])
        if request["no_op"]:
            result["preview"]["changes"][0]["action"] = "no-op"
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
        path = context.stage.parent / "container-apps-operation.json"
        if path.exists() or path.is_symlink():
            raise Refused("A Container Apps operation was already attempted; inspect it without replaying", 409)
        request = prepared.get("azure_request", {})
        if not request or fingerprint(request) != prepared.get("request_digest") or request.get("scope") != container_scope(context):
            raise Refused("Container Apps request lacks its reviewed recovery identity; prepare a fresh release", 409)
        before = json.loads(context.command(azure(context, "show"), capture=True))
        if container_request(context, before, image) != request:
            raise Refused("Container App changed after planning. Prepare a fresh release")

        # Same-image delivery observes the existing revision without manufacturing a new one
        record = {"request": request, "request_digest": prepared["request_digest"],
                  "status": "no-op" if request["no_op"] else "update-submitted"}
        durable_record(path, record)
        if not request["no_op"]:
            context.command(azure(context, "update", "--image", image, "--container-name", request["container"],
                                  "--revision-suffix", request["suffix"]), capture=True)
            record["status"] = "update-acknowledged"
            durable_record(path, record)

        # Native suffix uniqueness prevents collision but never grants permission to retry a write
        deadline = time.monotonic() + min(context.component.timeout_seconds, 240)
        while time.monotonic() < deadline:
            observed = container_inspect(context, prepared)
            if observed["status"] == "succeeded":
                return {key: observed[key] for key in ("revision", "image", "previous_revision", "no_op")}
            if observed["status"] == "failed":
                raise Refused("The requested Container Apps revision did not provision")
            context.process.heartbeat()
            time.sleep(2)
        raise Refused("The requested Container Apps revision did not converge; inspect it without replaying")
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
