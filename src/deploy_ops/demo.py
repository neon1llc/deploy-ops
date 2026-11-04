# src/deploy_ops/demo.py - Exercise the approval path without cloud accounts or network access.


import copy
import tempfile
from pathlib import Path

from .config import Settings, Target
from .errors import Refused
from .models import Claim, Completion, Decision, DelegationSpec, Proposal, Source
from .service import Controller
from .store import Store


# Define an offline policy fixture without real account access
def sample_target() -> Target:
    """Use obviously synthetic identities only in the local demonstration."""
    return Target(
        repository="example/application", repository_id="101", repository_owner_id="102",
        workflow_ref="example/authority/.github/workflows/deploy.yml@" + "a" * 40,
        environment="sandbox", planner_environment="sandbox-plan", cloud="aws", workdir="infra",
        identity={"role_arn": "arn:aws:iam::111111111111:role/demo-executor", "region": "us-east-1"},
        planner_identity={"role_arn": "arn:aws:iam::111111111111:role/demo-planner", "region": "us-east-1"},
        policy={"allow_delegation": True, "auto_types": ["aws_ecs_service"], "max_uses": 2},
    )


# Vary workflow stage and run while preserving the synthetic target binding
def sample_source(target: Target, stage: str = "plan", run: str = "1001") -> Source:
    return Source(
        repository=target.repository, repository_id=target.repository_id,
        repository_owner_id=target.repository_owner_id, ref=target.ref, sha="b" * 40,
        actor="example-developer", actor_id="103", run_id=run, run_attempt="1", event_name="workflow_dispatch",
        environment=target.planner_environment if stage == "plan" else target.environment,
        job_workflow_ref=target.workflow_ref, job_workflow_sha="a" * 40, subject="demo-only",
    )


# Provide one small plan with deterministic evidence digests
def sample_proposal() -> Proposal:
    return Proposal(
        target="sandbox", engine="opentofu", artifact_digest="c" * 64, lock_digest="d" * 64,
        tool_version="1.10.0", estimated_monthly_delta_cents=100,
        preview={"format_version": "1.2", "terraform_version": "1.10.0", "planned_values": {},
                 "resource_changes": [{"address": "aws_ecs_service.web", "type": "aws_ecs_service",
                                       "change": {"actions": ["update"], "after": {"desired_count": 2}}}]},
    )


# Exercise approval, replay refusal and delegation limits in temporary state
def run() -> dict:
    """Show successful paths and refusals with explicitly synthetic plan data."""
    results = []
    with tempfile.TemporaryDirectory(prefix="deploy_ops-demo-") as directory:
        target = sample_target()
        settings = Settings(targets={"sandbox": target}, database=str(Path(directory) / "audit.db"))
        clock = [1800000000]
        controller = Controller(settings, Store(settings.database), clock=lambda: clock[0])
        source = sample_source(target)
        request = controller.submit(sample_proposal(), source)
        controller.decide(request["id"], Decision(digest=request["digest"], decision="approve"), "demo-human")
        claim = Claim(digest=request["digest"], artifact_digest=request["manifest"]["artifact_digest"])
        lease = controller.claim(request["id"], claim, sample_source(target, "execute"))
        finished = controller.complete(request["id"], lease["token"], Completion(outcome="succeeded", exit_code=0))
        results.append({"scenario": "Human-approved exact plan", "result": finished["status"]})
        try:
            controller.claim(request["id"], claim, sample_source(target, "execute"))
        except Refused:
            results.append({"scenario": "Reusing a consumed approval", "result": "blocked"})
        controller.delegate(DelegationSpec(target="sandbox", hours=1, uses=1), "demo-human")
        automatic = controller.submit(sample_proposal(), sample_source(target, run="1002"))
        lease = controller.claim(automatic["id"], Claim(digest=automatic["digest"], artifact_digest="c" * 64),
                                 sample_source(target, "execute", "1002"))
        controller.complete(automatic["id"], lease["token"], Completion(outcome="succeeded", exit_code=0))
        results.append({"scenario": "Bounded unattended deployment", "result": "succeeded"})
        overflow = controller.submit(sample_proposal(), sample_source(target, run="1003"))
        results.append({"scenario": "Delegation use limit reached", "result": overflow["status"]})
        unsafe = copy.deepcopy(sample_proposal())
        unsafe.preview["resource_changes"][0]["type"] = "aws_iam_role"
        denied = controller.submit(unsafe, sample_source(target, run="1004"))
        results.append({"scenario": "IAM authority change", "result": denied["status"]})
        clock[0] += 3601
        expired = controller.get(overflow["id"])
        results.append({"scenario": "Expired pending request", "result": expired["status"]})
        controller.pause(True, "demo-human")
        audit = controller.store.verify()
    return {"mode": "offline demonstration with synthetic identities and plans",
            "cloud_calls": 0, "scenarios": results, "audit": audit}
