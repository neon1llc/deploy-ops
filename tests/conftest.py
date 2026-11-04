# tests/conftest.py - Share one small synthetic account across protocol checks.


import pytest

from deploy_ops.config import Settings
from deploy_ops.demo import sample_proposal, sample_source, sample_target
from deploy_ops.service import Controller
from deploy_ops.store import Store


# Give each protocol test an isolated ledger and controllable clock
@pytest.fixture
def system(tmp_path):
    target = sample_target()
    settings = Settings(targets={"sandbox": target}, database=str(tmp_path / "ledger.db"))
    clock = [1800000000]
    controller = Controller(settings, Store(settings.database), lambda: clock[0])
    return controller, target, clock


# Start each case with fresh synthetic plan evidence
@pytest.fixture
def proposal():
    return sample_proposal()


# Match the synthetic planning identity to the registered target
@pytest.fixture
def source(system):
    return sample_source(system[1])
