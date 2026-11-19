# src/deploy_ops/delivery/retained.py - Read original retained artifacts without constructing execution authority.


from pathlib import Path

from .engine import Engine
from .ledger import Ledger


# Expose only history and existing artifact-readback methods, with a read-only ledger
class Retained:
    # Leave configuration, grants, native clients, schema and runtime directories untouched
    def __init__(self, state: Path):
        self.state = state.resolve()
        self.ledger = Ledger.open_existing(self.state)
        self.deployments = self.state / 'deployments'


    # Preserve existing preview ownership and exact original absolute target semantics
    def deployment_root(self, release):
        return Engine.deployment_root(self, release)


    # Share envelope authentication and compatibility checks with the execution owner
    def _reviewed_envelope(self, release, name, reader=None):
        return Engine._reviewed_envelope(self, release, name, reader)
