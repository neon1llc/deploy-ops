# src/deploy_ops/delivery/scaffold.py - Copy a complete starter without replacing existing work.


import shutil
from importlib.resources import files
from pathlib import Path

from ..errors import Refused


# Preserve existing files when creating the packaged local starter
def create(destination: Path, template: str = "local") -> Path:
    """Refuse nonempty directories. Templates never install tools or create cloud resources."""
    if template not in ("local",):
        raise Refused("Only the complete local template is currently available", 422)
    if destination.exists() and any(destination.iterdir()):
        raise Refused("Choose an empty directory for the starter", 422)
    source = Path(str(files("deploy_ops").joinpath("templates", template)))
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination / "deploy-ops.toml"
