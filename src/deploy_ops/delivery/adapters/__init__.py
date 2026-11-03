# src/deploy_ops/delivery/adapters/__init__.py - Route delivery through one explicit adapter registry.

from . import cloud, iac, local, workflow

REGISTRY = {
    "directory": local, "static": local, "compose": local,
    "opentofu": iac, "terraform": iac,
    "ecs": cloud, "container-apps": cloud, "cloud-run": cloud, "kubernetes": cloud,
    "workflow": workflow,
}
