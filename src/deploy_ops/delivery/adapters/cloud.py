# src/deploy_ops/delivery/adapters/cloud.py - Deliver immutable application images with provider-specific checks.

import copy
import json
import re
import time

from ...client import private_json
from ...errors import Refused
from .base import Context, need, preview

IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
ECS_FIELDS = {
    "family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions",
    "volumes", "placementConstraints", "requiresCompatibilities", "cpu", "memory",
    "pidMode", "ipcMode", "proxyConfiguration", "inferenceAccelerators", "ephemeralStorage",
    "runtimePlatform", "enableFaultInjection",
}


def aws(context: Context, *parts: str) -> list[str]:
    return ["aws", *parts, "--region", need(context.options, "region"), "--output", "json", "--no-cli-pager"]


def azure(context: Context, *parts: str) -> list[str]:
    return ["az", "containerapp", *parts, "--name", need(context.options, "name"),
            "--resource-group", need(context.options, "resource_group"),
            "--subscription", need(context.options, "subscription_id"), "--output", "json"]


def google(context: Context, *parts: str) -> list[str]:
    return ["gcloud", "run", *parts, "--region", need(context.options, "region"),
            "--project", need(context.options, "project_id"), "--quiet", "--format=json"]


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
        task = json.loads(context.command(aws(context, "ecs", "describe-task-definition", "--task-definition",
            service["taskDefinition"]), capture=True))["taskDefinition"]
        definition = {key: value for key, value in task.items() if key in ECS_FIELDS}
        container = need(context.options, "container")
        matches = [item for item in definition["containerDefinitions"] if item["name"] == container]
        if len(matches) != 1:
            raise Refused("Expected exactly one named container in the ECS task")
        matches[0]["image"] = image
        private_json(context.stage / "task-definition.json", definition)
        result["previous_revision"] = service["taskDefinition"]
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


def apply(context: Context, prepared: dict) -> dict:
    adapter = context.component.adapter
    image = prepared["image"]
    if adapter == "ecs":
        cluster, service = need(context.options, "cluster"), need(context.options, "service")
        current = json.loads(context.command(aws(context, "ecs", "describe-services", "--cluster", cluster,
                                                "--services", service), capture=True))
        if current.get("failures") or current["services"][0]["taskDefinition"] != prepared["previous_revision"]:
            raise Refused("ECS service changed after planning. Prepare a fresh release")
        task = json.loads(context.command(aws(context, "ecs", "register-task-definition", "--cli-input-json",
            "file://" + str(context.stage / "task-definition.json")), capture=True))["taskDefinition"]["taskDefinitionArn"]
        context.command(aws(context, "ecs", "update-service", "--cluster", cluster, "--service", service,
                            "--task-definition", task), capture=True)
        context.command(aws(context, "ecs", "wait", "services-stable", "--cluster", cluster, "--services", service))
        state = json.loads(context.command(aws(context, "ecs", "describe-services", "--cluster", cluster,
                                              "--services", service), capture=True))["services"][0]
        if state["taskDefinition"] != task or any(d.get("rolloutState") == "FAILED" for d in state.get("deployments", [])):
            raise Refused("ECS did not converge to the requested revision. A rollback may have occurred")
        return {"revision": task, "image": image, "previous_revision": prepared["previous_revision"]}
    if adapter == "container-apps":
        before = json.loads(context.command(azure(context, "show"), capture=True))
        if before["properties"].get("latestReadyRevisionName") != prepared["previous_revision"]:
            raise Refused("Container App changed after planning. Prepare a fresh release")
        updated = json.loads(context.command(azure(context, "update", "--image", image), capture=True))
        revision = updated["properties"]["latestRevisionName"]
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
        traffic = json.loads(context.command(google(context, "services", "update-traffic", need(context.options, "name"),
            "--to-revisions", state["latestReadyRevisionName"] + "=100"), capture=True))
        serving = traffic.get("status", {}).get("traffic", [])
        if not any(item.get("revisionName") == state["latestReadyRevisionName"] and item.get("percent") == 100 for item in serving):
            raise Refused("Cloud Run traffic did not converge to the requested revision")
        return {"revision": state["latestReadyRevisionName"], "image": image, "url": state.get("url", ""),
                "previous_revision": prepared.get("previous_revision", "")}
    base = ["kubectl", "--context", need(context.options, "context"), "--namespace", need(context.options, "namespace")]
    deployment = "deployment/" + need(context.options, "deployment")
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
