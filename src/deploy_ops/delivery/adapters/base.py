# src/deploy_ops/delivery/adapters/base.py - Share execution context without hiding provider differences.


from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...errors import Refused
from ..process import Process
from ..spec import Component


# Carry one component's reviewed scope into its adapter
@dataclass
class Context:
    component: Component
    name: str
    environment: str
    provider: str
    source: Path
    stage: Path
    deployments: Path
    options: dict[str, Any]
    process: Process
    release_id: str


    # Use the shared process supervisor and component timeout
    def command(self, argv: list[str], capture: bool = False, input_text: str | None = None) -> str:
        return self.process.run(argv, self.source, self.component.timeout_seconds, capture, input_text)


# Reject missing values and strings that could become command options
def need(options: dict, name: str) -> str:
    value = options.get(name)
    if not isinstance(value, str) or not value or value.startswith("-") or "\x00" in value:
        raise Refused(f"Missing or invalid adapter option: {name}", 422)
    return value


# Describe a single adapter-level change with explicit review requirements
def preview(name: str, kind: str, manual: bool = True) -> dict:
    return {"changes": [{"address": name, "resource_type": kind, "action": "update"}],
            "findings": [], "manual_only": manual}
