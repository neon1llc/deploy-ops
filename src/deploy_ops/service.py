# src/deploy_ops/service.py - Own the approval, delegation, and execution state machine.

import copy
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Callable

from .config import Settings, Target, fingerprint
from .errors import Refused
from .models import Claim, Completion, Decision, DelegationSpec, Proposal, Resolution, Source
from .plans import normalize
from .policy import changed_resources, fits_delegation, hard_denial
from .store import Store


# Keep authorization transitions and audit events in one owner
class Controller:
    """Keep each state change atomic with its audit event."""

    # Share settings, storage and clock across every lifecycle operation
    def __init__(self, settings: Settings, store: Store, clock: Callable[[], float] = time.time):
        self.settings = settings
        self.store = store
        self.clock = clock

    # Use the injected clock consistently for authority windows
    def now(self) -> int:
        return int(self.clock())

    # Reject unregistered targets before considering caller evidence
    def target(self, name: str) -> Target:
        if name not in self.settings.targets:
            raise Refused("Target is not registered", 404)
        return self.settings.targets[name]

    # Match authenticated workflow claims to the requested stage
    def check_source(self, target: Target, source: Source, stage: str) -> None:
        if (target.local_cloud or target.local_isolated_iac) and source.kind != "local":
            raise Refused("Local cloud grants cannot be used by federated workers", 403)
        if source.kind == "local" and not (target.allow_local_operator and (target.cloud == "local" or target.local_cloud or target.local_isolated_iac)):
            raise Refused("Local operator authority cannot reach this target", 403)
        expected_environment = target.planner_environment if stage == "plan" else target.environment
        checks = (
            source.repository == target.repository,
            source.repository_id == target.repository_id,
            source.repository_owner_id == target.repository_owner_id,
            source.ref == target.ref,
            source.job_workflow_ref == target.workflow_ref,
            source.job_workflow_sha == (target.workflow_ref.rsplit("@", 1)[1] if target.workflow_ref else None),
            source.environment == expected_environment,
            source.event_name in target.events,
        )
        if not all(checks):
            raise Refused("Workflow identity is not authorized for this target and stage", 403)

    # Remove execution credential hashes from returned records
    @staticmethod
    def public(request: dict) -> dict:
        result = copy.deepcopy(request)
        if result.get("execution"):
            result["execution"].pop("token_hash", None)
        return result

    # Attach controller time and request identity to each audit entry
    def _event(self, connection, actor: str, name: str, request: dict, **detail) -> None:
        self.store.event(connection, self.now(), actor, name, request["id"], detail)

    # Refuse authority created under a different target policy
    def _current(self, request: dict) -> Target:
        target = self.target(request["manifest"]["target"])
        if target.digest != request["manifest"]["policy_digest"]:
            raise Refused("Target or policy changed. Submit a fresh plan")
        return target

    # Reject stale, superseded or cross-scope infrastructure evidence
    def _output_receipt(self, connection, receipt_id: str, consumer: Target) -> dict:
        request = self.store.request(connection, receipt_id)
        receipt = request.get("output_receipt")
        if not receipt or receipt["producer_target"] not in consumer.receipt_dependencies.values():
            raise Refused("Receipt producer is not registered for this consumer", 403)
        producer = self._current(request)
        from .receipts import scope
        if (receipt["environment"] != consumer.environment or scope(producer) != scope(consumer)):
            raise Refused("Receipt environment, cloud scope or region does not match this consumer", 403)
        if (request["status"] != "succeeded" or request.get("receipt_revoked") or request.get("receipt_invalidated_by")
                or self.now() >= receipt["expires_at"]):
            raise Refused("Infrastructure receipt is expired, revoked or unresolved")
        content = {key: value for key, value in receipt.items() if key != "digest"}
        if fingerprint(content) != receipt["digest"]:
            raise Refused("Infrastructure receipt content changed")
        busy = connection.execute("SELECT id FROM requests WHERE target=? AND status IN ('running','uncertain')",
                                  (receipt["producer_target"],)).fetchone()
        newer = any(json.loads(row[0]).get("output_receipt", {}).get("generation", 0) > receipt["generation"]
                    for row in connection.execute("SELECT data FROM requests WHERE target=? AND status='succeeded'",
                                                  (receipt["producer_target"],)))
        if busy or newer:
            raise Refused("Infrastructure has newer or unresolved work. Use a fresh receipt")
        return receipt

    # Require the complete registered dependency set with exact digests
    def _check_receipts(self, connection, target: Target, references: dict):
        if set(references) != set(target.receipt_dependencies):
            raise Refused("Provide exactly the registered infrastructure receipt dependencies", 422)
        for dependency, reference in references.items():
            receipt = self._output_receipt(connection, reference["id"], target)
            if receipt["producer_target"] != target.receipt_dependencies[dependency] or receipt["digest"] != reference["digest"]:
                raise Refused("Infrastructure receipt binding changed")

    # Authenticate the consumer before resolving a receipt
    def output_receipt(self, receipt_id: str, target_name: str, source: Source) -> dict:
        target = self.target(target_name)
        self.check_source(target, source, "execute")
        with self.store.connect() as connection:
            return self._output_receipt(connection, receipt_id, target)

    # Invalidate future receipt use in the same transaction as its audit event
    def revoke_receipt(self, receipt_id: str, approver: str) -> dict:
        with self.store.transaction() as connection:
            request = self.store.request(connection, receipt_id)
            if not request.get("output_receipt"):
                raise Refused("Infrastructure receipt not found", 404)
            request["receipt_revoked"] = True
            self.store.save_request(connection, request)
            self._event(connection, approver, "receipt.revoked", request)
        return {"id": receipt_id, "revoked": True}

    # Deduplicate exact evidence before applying denial or delegation rules
    def submit(self, proposal: Proposal, source: Source) -> dict:
        target = self.target(proposal.target)
        self.check_source(target, source, "execute" if proposal.engine == "delivery" else "plan")
        if proposal.engine == "delivery" and proposal.preview.get("config_digest") != target.delivery_digest:
            raise Refused("Workspace differs from its registered delivery contract", 403)
        if proposal.engine not in target.engines:
            raise Refused("This engine is not enabled for the target", 403)
        if target.receipt_outputs:
            from .receipts import verify_tool
            verify_tool(target.receipt_outputs.format, proposal.engine, proposal.tool_version)
            if proposal.preview.get("deploy_ops_backend") != target.receipt_outputs.backend.model_dump():
                raise Refused("Plan must bind the registered infrastructure backend", 422)
        preview = normalize(proposal.engine, target.cloud, proposal.preview)
        manifest = {
            "target": proposal.target, "cloud": target.cloud, "engine": proposal.engine,
            "artifact_digest": proposal.artifact_digest,
            "preview_digest": fingerprint(proposal.preview), "preview": preview.model_dump(),
            "tool_version": proposal.tool_version, "lock_digest": proposal.lock_digest,
            "source": source.model_dump(), "agent": proposal.agent.model_dump(),
            "estimated_monthly_delta_cents": proposal.estimated_monthly_delta_cents,
            "policy_digest": target.digest,
        }
        if target.local_isolated_iac and proposal.engine == "manifest":
            if (not proposal.isolated_packet
                    or fingerprint(proposal.isolated_packet.model_dump()) != target.identity["packet_digest"]
                    or proposal.artifact_digest != target.identity["packet_digest"]):
                raise Refused("Planning approval must include the exact registered public source packet", 403)
            manifest["isolated_packet"] = proposal.isolated_packet.model_dump()
        elif proposal.isolated_packet:
            raise Refused("Public source packets are only accepted for isolated planning approval", 422)
        if proposal.planning_request:
            manifest["planning_request"] = proposal.planning_request
        if proposal.receipts:
            manifest["receipts"] = {key: value.model_dump() for key, value in proposal.receipts.items()}
        digest = fingerprint(manifest)
        now = self.now()
        request = {
            "id": uuid.uuid4().hex, "digest": digest, "manifest": manifest,
            "created_at": now, "expires_at": now + self.settings.request_seconds,
            "status": "pending", "reason": "Waiting for human approval",
            "approval": None, "execution": None,
        }
        with self.store.transaction() as connection:
            # Local saved plans require a completed planning lease under the same frozen operator packet
            if target.local_isolated_iac and proposal.engine in {"terraform", "opentofu"}:
                if not proposal.planning_request:
                    raise Refused("Isolated apply requires a completed planning lease", 403)
                planning = self.store.request(connection, proposal.planning_request)
                parent = self._current(planning)
                if (planning["status"] != "succeeded" or planning["manifest"]["engine"] != "manifest"
                        or not parent.local_isolated_iac or parent.receipt_outputs
                        or parent.identity != target.planner_identity
                        or planning["manifest"]["artifact_digest"] != target.identity["packet_digest"]
                        or planning["manifest"]["source"]["run_id"] != source.run_id):
                    raise Refused("Planning lease does not authorize this isolated saved plan", 403)
            elif proposal.planning_request:
                raise Refused("Planning lease references are only supported for isolated native plans", 422)
            self._check_receipts(connection, target, manifest.get("receipts", {}))
            previous = connection.execute("SELECT data FROM requests WHERE digest=?", (digest,)).fetchone()
            if previous:
                return self.public(json.loads(previous["data"]))
            reason = hard_denial(target, manifest)
            if reason:
                request.update(status="denied", reason=reason)
            elif not self.store.paused(connection):
                for row in connection.execute("SELECT data FROM delegations ORDER BY rowid"):
                    grant = json.loads(row["data"])
                    if fits_delegation(target, manifest, grant, now):
                        request.update(status="approved", reason="Within an active delegation")
                        request["approval"] = {
                            "kind": "delegation", "by": grant["created_by"], "id": grant["id"],
                            "expires_at": min(request["expires_at"], grant["expires_at"]),
                        }
                        break
            self.store.save_request(connection, request)
            self._event(connection, f"{source.kind}:{source.actor_id}", "request.submitted", request,
                        digest=digest, status=request["status"], run_id=source.run_id)
        return self.public(request)

    # Compute expiry at read time without rewriting execution history
    def get(self, request_id: str, source: Source | None = None) -> dict:
        with self.store.connect() as connection:
            request = self.store.request(connection, request_id)
        if source is not None:
            target = self.target(request["manifest"]["target"])
            stage = "plan" if source.environment == target.planner_environment else "execute"
            self.check_source(target, source, stage)
        result = self.public(request)
        # Expiry is computed at the read boundary even when no worker is polling
        if result["status"] in ("pending", "approved") and self.now() >= self._deadline(result):
            result["status"] = "expired"
            result["reason"] = "Approval window expired. Submit a fresh request"
        return result

    # Return bounded history with current expiry interpretation
    def list_requests(self, limit: int = 100) -> list[dict]:
        with self.store.connect() as connection:
            rows = connection.execute("SELECT id FROM requests ORDER BY rowid DESC LIMIT ?", (limit,))
            ids = [row[0] for row in rows]
        return [self.get(request_id) for request_id in ids]

    # Use the earliest request or approval expiry
    @staticmethod
    def _deadline(request: dict) -> int:
        return min(request["expires_at"], (request["approval"] or {}).get("expires_at", request["expires_at"]))

    # Bind human review to current evidence before changing authority
    def decide(self, request_id: str, decision: Decision, approver: str, github_id: str = "") -> dict:
        with self.store.transaction() as connection:
            request = self.store.request(connection, request_id)
            if request["digest"] != decision.digest:
                raise Refused("Approval does not match the current request digest")
            if decision.decision == "approve":
                self._current(request)
                if github_id and github_id == request["manifest"]["source"]["actor_id"]:
                    raise Refused("The workflow initiator cannot approve their own request", 403)
                if self.store.paused(connection):
                    raise Refused("Controller is paused")
                if request["status"] != "pending" or self.now() >= request["expires_at"]:
                    raise Refused("Only a pending, unexpired request can receive approval")
            elif request["status"] not in ("pending", "approved", "rejected", "expired", "cancelled"):
                raise Refused("Started executions need reconciliation, not rejection")
            # Withdrawal is allowed until a transactional claim starts execution
            request["status"] = "approved" if decision.decision == "approve" else "rejected"
            request["reason"] = decision.note or f"Human decision: {decision.decision}"
            request["approval"] = {
                "kind": "human", "by": approver, "id": uuid.uuid4().hex,
                "expires_at": min(request["expires_at"], self.now() + self.settings.approval_seconds),
            }
            self.store.save_request(connection, request)
            self._event(connection, approver, "request.decided", request,
                        decision=decision.decision, digest=decision.digest, note=decision.note)
        return self.public(request)

    # Create a single bounded grant without stacking target allowances
    def delegate(self, spec: DelegationSpec, approver: str, delivery_id: str = "") -> dict:
        target = self.target(spec.target)
        if not target.policy.allow_delegation or target.production or not target.policy.auto_types:
            raise Refused("Unattended delegation is disabled for this target", 403)
        if spec.hours > self.settings.delegation_hours or spec.uses > target.policy.max_uses:
            raise Refused("Requested delegation exceeds the administrator's limits", 403)
        now = self.now()
        grant = {
            "id": uuid.uuid4().hex, "target": spec.target, "policy_digest": target.digest,
            "starts_at": now, "expires_at": now + spec.hours * 3600,
            "active": True, "max_uses": spec.uses, "uses": 0, "changes": 0,
            "estimated_cents": 0, "created_by": approver,
        }
        with self.store.transaction() as connection:
            if self.store.paused(connection):
                raise Refused("Controller is paused")
            if delivery_id:
                self._deduplicate(connection, delivery_id)
            # One active window per target prevents stacking several small grants
            for row in connection.execute("SELECT data FROM delegations"):
                current = json.loads(row["data"])
                if current["target"] == spec.target and current["active"] and current["expires_at"] > now:
                    raise Refused("An active delegation already exists for this target")
            self.store.save_delegation(connection, grant)
            self._event(connection, approver, "delegation.created", grant, target=spec.target,
                        expires_at=grant["expires_at"], max_uses=spec.uses)
        return grant

    # Reject replayed signed commands within the replay window
    def _deduplicate(self, connection, delivery_id: str) -> None:
        connection.execute("DELETE FROM deliveries WHERE expires_at < ?", (self.now(),))
        if connection.execute("SELECT 1 FROM deliveries WHERE id=?", (delivery_id,)).fetchone():
            raise Refused("This signed command was already handled")
        connection.execute("INSERT INTO deliveries VALUES (?, ?)", (delivery_id, self.now() + 600))

    # Withdraw grant authority while preserving uncertain execution evidence
    def revoke(self, delegation_id: str, approver: str) -> dict:
        with self.store.transaction() as connection:
            grant = self.store.delegation(connection, delegation_id)
            grant["active"] = False
            self.store.save_delegation(connection, grant)
            for row in connection.execute("SELECT data FROM requests WHERE status='running'").fetchall():
                request = json.loads(row["data"])
                if request["approval"]["id"] == delegation_id:
                    request.update(status="uncertain", reason="Delegation revoked during execution")
                    self.store.save_request(connection, request)
            self._event(connection, approver, "delegation.revoked", grant)
        return grant

    # Revoke outstanding work without pretending accepted cloud calls rolled back
    def pause(self, paused: bool, approver: str, delivery_id: str = "") -> dict:
        with self.store.transaction() as connection:
            if delivery_id:
                self._deduplicate(connection, delivery_id)
            connection.execute(
                "INSERT INTO settings VALUES ('paused', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("true" if paused else "false",),
            )
            if paused:
                for row in connection.execute("SELECT data FROM delegations").fetchall():
                    grant = json.loads(row["data"])
                    grant["active"] = False
                    self.store.save_delegation(connection, grant)
                rows = connection.execute(
                    "SELECT data FROM requests WHERE status IN ('approved','running')"
                ).fetchall()
                for row in rows:
                    request = json.loads(row["data"])
                    request.update(status="uncertain" if request["status"] == "running" else "cancelled",
                                   reason="Controller paused by an operator")
                    self.store.save_request(connection, request)
            self.store.event(
                connection, self.now(), approver,
                "controller.paused" if paused else "controller.resumed", "controller", {},
            )
        return {"paused": paused}

    # Atomically bind one execution and consume its delegation allowance
    def claim(self, request_id: str, claim: Claim, source: Source) -> dict:
        with self.store.transaction() as connection:
            request = self.store.request(connection, request_id)
            target = self._current(request)
            self.check_source(target, source, "execute")
            original = request["manifest"]["source"]
            # A rerun is a new attempt and needs a new plan, not an old approval
            if any(getattr(source, key) != original[key] for key in ("sha", "run_id", "run_attempt", "actor_id")):
                raise Refused("Execution must match the approved commit, run, attempt, and initiator", 403)
            if claim.digest != request["digest"] or claim.artifact_digest != request["manifest"]["artifact_digest"]:
                raise Refused("Artifact or request digest changed")
            self._check_receipts(connection, target, request["manifest"].get("receipts", {}))
            now = self.now()
            if self.store.paused(connection) or request["status"] != "approved":
                raise Refused("Request is not available for execution")
            deadline = min(self._deadline(request), now + self.settings.execution_seconds)
            if deadline - now < 30:
                raise Refused("Approval has expired or has less than 30 seconds remaining")
            busy = connection.execute(
                "SELECT id FROM requests WHERE target=? AND status IN ('running','uncertain')",
                (request["manifest"]["target"],),
            ).fetchone()
            if busy:
                raise Refused("Target already has a running or unresolved execution")
            if request["approval"]["kind"] == "delegation":
                grant = self.store.delegation(connection, request["approval"]["id"])
                if not fits_delegation(target, request["manifest"], grant, now):
                    raise Refused("Delegation expired, was revoked, or has insufficient remaining scope")
                grant["uses"] += 1
                grant["changes"] += len(changed_resources(request["manifest"]))
                grant["estimated_cents"] += max(0, request["manifest"]["estimated_monthly_delta_cents"] or 0)
                self.store.save_delegation(connection, grant)
            token = "dgexec_" + secrets.token_urlsafe(32)
            request.update(status="running", reason="Claimed by the trusted executor")
            request["execution"] = {
                "started_at": now, "deadline": deadline, "last_seen": now,
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "reported_outcome": None,
                "source": source.model_dump(),
            }
            if target.receipt_outputs:
                # A failed apply can still mutate infrastructure; never revive its predecessor's receipt
                for row in connection.execute("SELECT data FROM requests WHERE target=?", (request["manifest"]["target"],)).fetchall():
                    previous = json.loads(row[0])
                    if previous.get("output_receipt") and not previous.get("receipt_invalidated_by"):
                        previous["receipt_invalidated_by"] = request_id
                        self.store.save_request(connection, previous)
                        self._event(connection, "controller", "receipt.invalidated", previous, execution_id=request_id)
            self.store.save_request(connection, request)
            self._event(connection, f"{source.kind}:{source.actor_id}", "execution.claimed", request,
                        approval_id=request["approval"]["id"], deadline=deadline)
            receipt = self._receipt(request, target)
            receipt["token"] = token
        return receipt

    # Return only the registered execution context needed by the runner
    @staticmethod
    def _receipt(request: dict, target: Target) -> dict:
        manifest = request["manifest"]
        return {
            "request_id": request["id"], "digest": request["digest"],
            "artifact_digest": manifest["artifact_digest"], "engine": manifest["engine"],
            "tool_version": manifest["tool_version"], "lock_digest": manifest["lock_digest"],
            "commit": manifest["source"]["sha"], "deadline": request["execution"]["deadline"],
            "cloud": target.cloud, "identity": target.identity, "workdir": target.workdir,
            "receipt_outputs": target.receipt_outputs.model_dump() if target.receipt_outputs else None,
        }

    # Compare the presented credential against its stored hash
    @staticmethod
    def _check_execution_token(request: dict, token: str) -> None:
        expected = (request.get("execution") or {}).get("token_hash", "")
        actual = hashlib.sha256(token.encode()).hexdigest()
        if not expected or not hmac.compare_digest(expected, actual):
            raise Refused("Invalid execution credential", 401)

    # Persist authority loss as uncertainty before refusing continued execution
    def heartbeat(self, request_id: str, token: str) -> dict:
        failure = None
        with self.store.transaction() as connection:
            request = self.store.request(connection, request_id)
            self._check_execution_token(request, token)
            try:
                target = self._current(request)
                self._check_receipts(connection, target, request["manifest"].get("receipts", {}))
                if request["status"] != "running" or self.store.paused(connection):
                    raise Refused("Execution is no longer authorized")
                if self.now() >= request["execution"]["deadline"]:
                    raise Refused("Execution window expired")
                if request["approval"]["kind"] == "delegation":
                    grant = self.store.delegation(connection, request["approval"]["id"])
                    if not grant["active"] or self.now() >= grant["expires_at"]:
                        raise Refused("Delegation is no longer active")
            except Refused as error:
                failure = error
                if request["status"] == "running":
                    request.update(status="uncertain", reason=str(error))
                    self.store.save_request(connection, request)
                    self._event(connection, "executor", "execution.interrupted", request, reason=str(error))
            else:
                request["execution"]["last_seen"] = self.now()
                self.store.save_request(connection, request)
                receipt = self._receipt(request, target)
        # Persist the uncertain state before returning an authorization failure
        if failure:
            raise failure
        return receipt

    # Validate reported outputs before recording the final execution result
    def complete(self, request_id: str, token: str, result: Completion) -> dict:
        with self.store.transaction() as connection:
            request = self.store.request(connection, request_id)
            self._check_execution_token(request, token)
            execution = request["execution"]
            if execution["reported_outcome"] is not None:
                if result.infrastructure and execution["reported_outcome"] == result.model_dump() and request.get("output_receipt"):
                    return self.public(request)
                raise Refused("An execution outcome was already reported")
            target = self.settings.targets.get(request["manifest"]["target"])
            if request["status"] == "running" and request["manifest"].get("receipts"):
                try:
                    self._check_receipts(connection, self._current(request), request["manifest"]["receipts"])
                except Refused:
                    request.update(status="uncertain", reason="Infrastructure evidence changed during execution")
            if result.infrastructure:
                target = self._current(request)
                if not target.receipt_outputs or result.outcome != "succeeded" or result.exit_code != 0:
                    raise Refused("Public outputs require a successful configured infrastructure execution", 422)
                if request["status"] != "running" or self.now() >= execution["deadline"] or self.store.paused(connection):
                    raise Refused("Uncertain or expired execution cannot issue an infrastructure receipt")
                self._check_receipts(connection, target, request["manifest"].get("receipts", {}))
                from .receipts import issue
                for row in connection.execute("SELECT data FROM requests WHERE target=? AND status='succeeded'",
                                              (request["manifest"]["target"],)).fetchall():
                    previous = json.loads(row[0]).get("output_receipt")
                    if previous and previous["policy_digest"] == target.digest:
                        evidence = result.infrastructure
                        if (previous["state_lineage"] != evidence.state_lineage or previous["state_serial"] > evidence.state_serial
                                or (previous["state_serial"] == evidence.state_serial and previous["state_digest"] != evidence.state_digest)):
                            raise Refused("Infrastructure state lineage or serial moved backwards or changed unexpectedly")
                generation = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM audit").fetchone()[0]
                request["output_receipt"] = issue(request, target, result.infrastructure, self.now(), generation)
            elif target and target.receipt_outputs and result.outcome == "succeeded":
                raise Refused("Successful infrastructure execution must include its public output receipt", 422)
            execution["reported_outcome"] = result.model_dump()
            execution["finished_at"] = self.now()
            # Expiry, interruption, and controller loss need human reconciliation
            if request["status"] == "running":
                if self.now() >= execution["deadline"] or result.outcome == "aborted":
                    request.update(status="uncertain", reason="Inspect infrastructure before resolving this execution")
                else:
                    request.update(status=result.outcome, reason="Outcome reported by the trusted runner")
            self.store.save_request(connection, request)
            self._event(connection, "executor", "execution.reported", request, **result.model_dump())
        return self.public(request)

    # Require explicit inspection to close running or uncertain work
    def resolve(self, request_id: str, resolution: Resolution, approver: str) -> dict:
        with self.store.transaction() as connection:
            request = self.store.request(connection, request_id)
            if request["status"] not in ("running", "uncertain"):
                raise Refused("Only running or uncertain executions need reconciliation")
            request.update(status=resolution.outcome, reason=resolution.note)
            self.store.save_request(connection, request)
            self._event(connection, approver, "execution.resolved", request, **resolution.model_dump())
        return self.public(request)
