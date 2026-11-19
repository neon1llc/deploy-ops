# tests/test_controller.py - Check authority boundaries across complete request lifecycles.


import concurrent.futures
import copy
import json

import pytest

from deploy_ops.demo import sample_source
from deploy_ops.errors import Refused
from deploy_ops.models import Claim, Completion, Decision, DelegationSpec, Resolution


# Versioned caller labels preserve legacy approved digests and verified identity
def test_neutral_caller_contract_preserves_legacy_requests(system, proposal, source):
    from deploy_ops.config import canonical, fingerprint
    from deploy_ops.models import Caller, Proposal
    from deploy_ops.service import Controller
    controller, target, _ = system
    legacy_body = proposal.model_dump(exclude={'schema_version', 'caller', 'agent'})
    legacy_body['agent'] = {'label': 'legacy-caller', 'model': 'retained-model-label', 'run': 'old-run'}
    legacy = controller.submit(Proposal.model_validate(legacy_body), source)
    assert 'schema_version' not in legacy['manifest']
    assert fingerprint(legacy['manifest']) == legacy['digest']
    approved = approve(controller, legacy)
    old_bytes = canonical(approved['manifest'])
    audit = controller.store.audit()
    neutral = controller.submit(Proposal.model_validate({**proposal.model_dump(),
                                'caller': Caller(label='release-job', run='build-1').model_dump()}), source)
    assert neutral['manifest']['schema_version'] == 2
    assert neutral['manifest']['caller']['label'] == 'release-job'
    assert 'agent' not in neutral['manifest']
    assert 'model' not in neutral['manifest']['caller']
    assert neutral['manifest']['source'] == source.model_dump()
    assert neutral['digest'] != legacy['digest']
    reopened = Controller(controller.settings, controller.store, controller.now)
    assert canonical(reopened.get(legacy['id'])['manifest']) == old_bytes
    assert reopened.get(legacy['id'])['digest'] == approved['digest']
    assert controller.store.audit()[:len(audit)] == audit
    assert claim(reopened, target, approved)['request_id'] == legacy['id']


# Mixed and unknown new caller fields are refused rather than silently discarded
@pytest.mark.parametrize('change', [{'caller': {'model': 'unnecessary'}},
    {'schema_version': 2, 'agent': {'label': 'legacy'}},
    {'schema_version': 1, 'caller': {'label': 'new'}}])
def test_caller_contract_refuses_mixed_metadata(proposal, change):
    from pydantic import ValidationError
    from deploy_ops.models import Proposal
    with pytest.raises(ValidationError):
        Proposal.model_validate({**proposal.model_dump(), **change})


# Use a separate synthetic human for exact-digest approval
@pytest.mark.parametrize('uncertain', [False, True])
def test_active_blocker_outlives_bounded_history(system, proposal, source, uncertain):
    controller, target, _ = system
    first = approve(controller, controller.submit(proposal, source))
    claim(controller, target, first)
    if uncertain:
        controller.pause(True, 'human')
        controller.pause(False, 'human')
    for index in range(105):
        request = controller.submit(proposal.model_copy(update={'artifact_digest': f'{index + 1:064x}'}), source)
        controller.decide(request['id'], Decision(digest=request['digest'], decision='reject'), 'human')
    assert len(controller.list_requests()) == 100
    assert first['id'] not in {item['id'] for item in controller.list_requests()}
    assert first['id'] in {item['id'] for item in controller.active_requests()}
    assert controller.active_requests(['other-target']) == []
    last = approve(controller, controller.submit(proposal.model_copy(update={'artifact_digest': 'e' * 64}), source))
    with pytest.raises(Refused, match=first['id']):
        claim(controller, target, last)
    assert controller.get(last['id'])['status'] == 'approved'


# Use a separate synthetic human for exact-digest approval
def approve(controller, request):
    return controller.decide(request["id"], Decision(digest=request["digest"], decision="approve"), "human")


# Native resource names retain the same default protection as Terraform types
@pytest.mark.parametrize("kind", ["AWS::IAM::Role", "AWS::EC2::SecurityGroup", "AWS::Logs::LogGroup",
                                  "Microsoft.Authorization/roleAssignments", "Microsoft.KeyVault/vaults"])
def test_native_resource_types_preserve_default_denials(system, kind):
    from deploy_ops.policy import hard_denial
    manifest = {"preview": {"findings": [], "changes": [{"address": "resource", "resource_type": kind, "action": "create"}]}}
    assert "outside" in hard_denial(system[1], manifest)


# Keep execution identity aligned with the requested run
def claim(controller, target, request, run="1001"):
    return controller.claim(request["id"], Claim(digest=request["digest"], artifact_digest=request["manifest"]["artifact_digest"]),
                            sample_source(target, "execute", run))


# A completed approval cannot authorize another execution
def test_manual_lifecycle_and_replay(system, proposal, source):
    controller, target, _ = system
    request = controller.submit(proposal, source)
    assert controller.submit(proposal, source)["id"] == request["id"]
    assert request["status"] == "pending"
    approve(controller, request)
    lease = claim(controller, target, request)
    assert "token_hash" not in controller.get(request["id"])["execution"]
    assert controller.heartbeat(request["id"], lease["token"])["artifact_digest"] == proposal.artifact_digest
    assert controller.complete(request["id"], lease["token"], Completion(outcome="succeeded", exit_code=0))["status"] == "succeeded"
    with pytest.raises(Refused):
        claim(controller, target, request)
    assert controller.store.verify()["events"] == 4


# A valid token alone does not authorize a different source or stage
@pytest.mark.parametrize("field,value", [
    ("repository_id", "999"), ("repository_owner_id", "999"),
    ("repository", "attacker/application"), ("ref", "refs/heads/untrusted"),
    ("job_workflow_ref", "attacker/runner/.github/workflows/deploy.yml@" + "a" * 40),
    ("job_workflow_sha", "e" * 40), ("environment", "sandbox"),
    ("event_name", "pull_request_target"),
])
def test_verified_identity_must_match_target(system, proposal, source, field, value):
    changed = source.model_copy(update={field: value})
    with pytest.raises(Refused, match="identity"):
        system[0].submit(proposal, changed)


# Changing any bound evidence invalidates the execution claim
@pytest.mark.parametrize("change", ["artifact", "digest", "attempt", "commit", "policy", "expiry"])
def test_stale_or_changed_authority_cannot_execute(system, proposal, source, change):
    controller, target, clock = system
    request = controller.submit(proposal, source)
    approve(controller, request)
    identity = sample_source(target, "execute")
    body = Claim(digest=request["digest"], artifact_digest=proposal.artifact_digest)
    if change == "artifact":
        body.artifact_digest = "e" * 64
    elif change == "digest":
        body.digest = "e" * 64
    elif change == "attempt":
        identity.run_attempt = "2"
    elif change == "commit":
        identity.sha = "f" * 40
    elif change == "policy":
        target.policy.max_changes += 1
    elif change == "expiry":
        clock[0] += 3601
    with pytest.raises(Refused):
        controller.claim(request["id"], body, identity)


# Failed executions still consume the finite grant allowance
def test_delegation_counts_at_claim_and_cannot_stack(system, proposal, source):
    controller, target, _ = system
    controller.delegate(DelegationSpec(target="sandbox", hours=1, uses=1), "human")
    with pytest.raises(Refused, match="already exists"):
        controller.delegate(DelegationSpec(target="sandbox", hours=1, uses=1), "human")
    first = controller.submit(proposal, source)
    second = controller.submit(proposal, sample_source(target, run="1002"))
    assert first["status"] == second["status"] == "approved"
    lease = claim(controller, target, first)
    controller.complete(first["id"], lease["token"], Completion(outcome="failed", exit_code=1))
    with pytest.raises(Refused, match="remaining scope"):
        claim(controller, target, second, "1002")


# Cost uncertainty cannot enter the unattended path
@pytest.mark.parametrize("field,value", [("estimated_monthly_delta_cents", None), ("estimated_monthly_delta_cents", 5000)])
def test_unknown_or_large_estimates_need_human_review(system, proposal, source, field, value):
    controller = system[0]
    controller.delegate(DelegationSpec(target="sandbox", hours=1, uses=1), "human")
    proposal = proposal.model_copy(update={field: value})
    assert controller.submit(proposal, source)["status"] == "pending"


# Human approval cannot expand the controller's hard authority boundary
@pytest.mark.parametrize("action,resource", [("update", "aws_iam_role"), ("delete", "aws_ecs_service"), ("update", "aws_cloudtrail")])
def test_denied_changes_cannot_be_manually_overridden(system, proposal, source, action, resource):
    proposal.preview["resource_changes"][0].update(type=resource, change={"actions": [action]})
    request = system[0].submit(proposal, source)
    assert request["status"] == "denied"
    with pytest.raises(Refused):
        approve(system[0], request)


# Concurrent consumers must share one transactional approval claim
def test_concurrent_claims_have_one_winner(system, proposal, source):
    controller, target, _ = system
    request = controller.submit(proposal, source)
    approve(controller, request)


    # Treat only a successful claim as a winning worker
    def attempt(_):
        try:
            claim(controller, target, request)
            return True
        except Refused:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as workers:
        assert sum(workers.map(attempt, range(6))) == 1


# Revocation and expiry preserve uncertainty until explicit inspection
@pytest.mark.parametrize("stop", ["pause", "revoke", "expire", "policy"])
def test_interruptions_require_reconciliation(system, proposal, source, stop):
    controller, target, clock = system
    grant = controller.delegate(DelegationSpec(target="sandbox", hours=1, uses=2), "human")
    request = controller.submit(proposal, source)
    lease = claim(controller, target, request)
    if stop == "pause":
        controller.pause(True, "human")
        controller.pause(False, "human")
    elif stop == "revoke":
        controller.revoke(grant["id"], "human")
    elif stop == "expire":
        clock[0] = lease["deadline"]
    else:
        target.policy.max_changes += 1
    with pytest.raises(Refused):
        controller.heartbeat(request["id"], lease["token"])
    assert controller.get(request["id"])["status"] == "uncertain"
    result = controller.complete(request["id"], lease["token"], Completion(outcome="aborted"))
    assert result["status"] == "uncertain"
    resolved = controller.resolve(request["id"], Resolution(outcome="failed", note="Checked cloud state and stopped the runner"), "human")
    assert resolved["status"] == "failed"


# Review and execution require distinct valid authority
def test_self_approval_and_wrong_execution_credential_are_rejected(system, proposal, source):
    controller, target, _ = system
    request = controller.submit(proposal, source)
    decision = Decision(digest=request["digest"], decision="approve")
    with pytest.raises(Refused, match="own request"):
        controller.decide(request["id"], decision, "slack:human", source.actor_id)
    approve(controller, request)
    claim(controller, target, request)
    with pytest.raises(Refused, match="credential"):
        controller.heartbeat(request["id"], "not-a-real-execution-secret")


# Retain change evidence without leaking raw plan values
def test_audit_detects_changed_events_without_storing_plan_values(system, proposal, source):
    controller = system[0]
    proposal.preview["resource_changes"][0]["change"]["after"]["secret"] = "do-not-retain-this-value"
    request = controller.submit(proposal, source)
    assert "do-not-retain" not in json.dumps(request)
    assert "do-not-retain" not in json.dumps(controller.store.audit())
    with controller.store.transaction() as connection:
        connection.execute("UPDATE audit SET payload='{}' WHERE sequence=1")
    with pytest.raises(Refused, match="Audit chain"):
        controller.store.verify()


# Unknown tool guarantees require manual review even for allowed resource types
def test_generic_manifests_never_inherit_automatic_delegation(system, source):
    from deploy_ops.demo import sample_proposal
    controller, target, _ = system
    target.engines.append("manifest")
    controller.delegate(DelegationSpec(target="sandbox", hours=1, uses=1), "human")
    proposal = sample_proposal().model_copy(update={"engine": "manifest", "preview": {
        "changes": [{"address": "web", "resource_type": "aws_ecs_service", "action": "update"}],
    }})
    assert controller.submit(proposal, source)["status"] == "pending"
