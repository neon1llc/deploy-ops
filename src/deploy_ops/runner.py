# src/deploy_ops/runner.py - Apply an approved saved plan on a separate trusted runner.

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from .client import Client, file_digest, github_token, private_json
from .clouds import environment
from .errors import Refused
from .models import Agent, Proposal

BINARIES = {"terraform": "terraform", "opentofu": "tofu"}


def executable(engine: str) -> str:
    if engine not in BINARIES or not (binary := shutil.which(BINARIES[engine])):
        raise Refused("Install the pinned Terraform or OpenTofu binary on the trusted runner", 422)
    return binary


def workdir(workspace: Path, relative: str, commit: str) -> Path:
    root = workspace.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_dir():
        raise Refused("Configured working directory is outside the repository", 422)
    current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                             capture_output=True, text=True).stdout.strip()
    if current != commit:
        raise Refused("Checked-out commit differs from the approved commit")
    # Untracked artifacts are allowed, but tracked infrastructure must match the commit
    result = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=root)
    if result.returncode != 0:
        raise Refused("Tracked files differ from the approved commit")
    return path


def clean_environment(home: str, credentials: dict) -> dict[str, str]:
    """Do not pass human credentials, GitHub tokens, or CLI injection flags to providers."""
    inherited = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE")
    result = {key: value for key, value in os.environ.items() if key in inherited}
    result.update(HOME=home, TF_IN_AUTOMATION="1", TF_INPUT="0", **credentials)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for key, value in credentials.items():
            if any(part in key for part in ("TOKEN", "SECRET", "ACCESS_KEY")):
                escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print("::add-mask::" + escaped, flush=True)
    return result


def _stop(process: subprocess.Popen) -> None:
    # Clean up the process group even if a provider outlives the CLI process
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=3)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def supervised(command: list[str], cwd: Path, env: dict, heartbeat, deadline: int) -> int:
    """Stop local execution when authority expires or the controller cannot be reached."""
    if os.name != "posix":
        raise Refused("The supervised runner currently requires macOS or Linux", 422)
    heartbeat()
    if time.time() >= deadline:
        raise Refused("Execution deadline reached")
    process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    next_check = time.monotonic() + 5
    try:
        while process.poll() is None:
            if time.time() >= deadline:
                raise Refused("Execution deadline reached")
            if time.monotonic() >= next_check:
                heartbeat()
                next_check = time.monotonic() + 5
            time.sleep(0.2)
        return process.returncode
    finally:
        _stop(process)


def execute(client: Client, lease: dict, artifact: Path, workspace: Path) -> int:
    """Recheck authority online before reading credentials or starting a cloud command."""
    request_id, token = lease["request_id"], lease["token"]
    route = f"/v1/executions/{request_id}"

    def heartbeat():
        return client.call("POST", route + "/heartbeat", execution_token=token)

    authority = heartbeat()
    if authority["engine"] not in BINARIES:
        raise Refused("Generic manifests use an existing trusted deployment engine, not this runner", 422)
    result, outcome, infrastructure = None, "aborted", None
    try:
        binary = executable(authority["engine"])
        directory = workdir(workspace, authority["workdir"], authority["commit"])
        if file_digest(directory / ".terraform.lock.hcl") != authority["lock_digest"]:
            raise Refused("Provider lock changed after planning")
        version = json.loads(subprocess.run([binary, "version", "-json"], check=True,
                                            capture_output=True, text=True).stdout)["terraform_version"]
        if version != authority["tool_version"]:
            raise Refused("The planning and execution tool versions differ")
        with tempfile.TemporaryDirectory(prefix="deploy_ops-") as temporary:
            snapshot = Path(temporary) / "approved.plan"
            # Hash the copy that will actually be applied, closing a source-file replacement race
            shutil.copyfile(artifact, snapshot)
            snapshot.chmod(0o400)
            if file_digest(snapshot) != authority["artifact_digest"]:
                raise Refused("Saved plan differs from the approved artifact")
            with httpx.Client(timeout=10, follow_redirects=False) as http:
                credentials = environment(authority["cloud"], authority["identity"], github_token, request_id, http)
            env = clean_environment(temporary, credentials)
            result = supervised([binary, "init", "-input=false", "-lockfile=readonly", "-no-color"],
                                directory, env, heartbeat, authority["deadline"])
            if result == 0:
                if authority.get("receipt_outputs"):
                    from .receipts import verify_backend
                    verify_backend(directory, authority["receipt_outputs"]["backend"])
                # Entra consumes a short-lived assertion when the provider starts
                if authority["cloud"] == "azure":
                    env.update(clean_environment(temporary, {
                        "ARM_OIDC_TOKEN": github_token("api://AzureADTokenExchange")
                    }))
                result = supervised([binary, "apply", "-input=false", "-no-color", str(snapshot)],
                                    directory, env, heartbeat, authority["deadline"])
            if result == 0 and authority.get("receipt_outputs"):
                # Raw state stays in this trusted process; only typed public fields cross the boundary
                from .receipts import extract_state
                from .delivery.process import Process
                verify_backend(directory, authority["receipt_outputs"]["backend"])
                reader = Process(Path(temporary), lambda *_: None, credentials, heartbeat, authority["deadline"])
                raw = reader.run([binary, "state", "pull"], directory, timeout=60, capture=True)
                infrastructure = extract_state(raw.encode()).model_dump()
                heartbeat()
            outcome = "succeeded" if result == 0 else "failed"
    finally:
        # A failed report leaves the target locked until an operator checks the infrastructure
        completed = client.call("POST", route + "/complete", {"outcome": outcome, "exit_code": result,
                                                  "infrastructure": infrastructure if outcome == "succeeded" else None},
                    execution_token=token)
    if outcome == "succeeded" and completed["status"] != "succeeded":
        raise Refused("Execution finished but the controller requires reconciliation")
    return result or 0


def plan(client: Client, target: str, engine: str, workspace: Path, output: Path,
         agent: Agent, cost_file: Path | None = None) -> dict:
    """Create a saved plan using the controller's separate planning identity."""
    context = client.call("GET", f"/v1/targets/{target}/plan-context")
    if engine not in context["engines"] or engine not in BINARIES:
        raise Refused("Engine is not enabled for guarded planning", 422)
    directory = workdir(workspace, context["workdir"], context["commit"])
    binary = executable(engine)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact = output.resolve() / "plan.bin"
    lock = directory / ".terraform.lock.hcl"
    if not lock.is_file():
        raise Refused("Commit a reviewed .terraform.lock.hcl before using this workflow", 422)
    with tempfile.TemporaryDirectory(prefix="deploy_ops-plan-") as home:
        with httpx.Client(timeout=10, follow_redirects=False) as http:
            credentials = environment(context["cloud"], context["identity"], github_token,
                                      "plan-" + context["commit"][:12], http)
        env = clean_environment(home, credentials)
        subprocess.run([binary, "init", "-input=false", "-lockfile=readonly", "-no-color"],
                       cwd=directory, env=env, check=True, timeout=300)
        if context.get("receipt_outputs"):
            from .receipts import verify_backend
            verify_backend(directory, context["receipt_outputs"]["backend"])
        if context["cloud"] == "azure":
            env.update(clean_environment(home, {
                "ARM_OIDC_TOKEN": github_token("api://AzureADTokenExchange")
            }))
        subprocess.run([binary, "plan", "-input=false", "-no-color", "-out=" + str(artifact)],
                       cwd=directory, env=env, check=True, timeout=600)
        artifact.chmod(0o600)
        raw = json.loads(subprocess.run([binary, "show", "-json", str(artifact)],
                                       cwd=directory, env=env, check=True, capture_output=True,
                                       text=True, timeout=60).stdout)
        if context.get("receipt_outputs"):
            raw["deploy_ops_backend"] = context["receipt_outputs"]["backend"]
        version = json.loads(subprocess.run([binary, "version", "-json"], check=True,
                                            capture_output=True, text=True).stdout)["terraform_version"]
    from .plans import cost_delta
    cost = cost_delta(json.loads(cost_file.read_text())) if cost_file else None
    proposal = Proposal(target=target, engine=engine, artifact_digest=file_digest(artifact),
                        preview=raw, tool_version=version, lock_digest=file_digest(lock),
                        estimated_monthly_delta_cents=cost, agent=agent)
    request = client.call("POST", "/v1/requests", proposal.model_dump())
    private_json(output / "ticket.json", {"id": request["id"], "digest": request["digest"]})
    return request
