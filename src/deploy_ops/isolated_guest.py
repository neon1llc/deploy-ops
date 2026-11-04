# src/deploy_ops/isolated_guest.py - Supervise pinned native IaC inside a dedicated Linux guest.
# Install this standard-library-only helper as root under /opt/deployops-tools.


import base64
import contextlib
import fcntl
import hashlib
import grp
import json
import os
from pathlib import Path
import pwd
import queue
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

# Fixed paths prevent a source packet from selecting executable code
TOOLS = {"terraform": ("terraform", "1.12.2"), "opentofu": ("tofu", "1.10.0")}
PROVIDER = "registry.opentofu.org/hashicorp/google"
PROVIDER_FILE = "/opt/deployops-tools/providers/registry.opentofu.org/hashicorp/google/6.50.0/linux_amd64/terraform-provider-google"
LIMIT = 12_000_000


# Retain only a fixed native command name and numeric exit status
class NativeFailure(RuntimeError):
    # Exclude arbitrary command arguments from the public failure record
    def __init__(self, stage, exit_code):
        self.stage = stage if stage in {"version", "init", "plan", "show", "apply", "state"} else "native"
        self.exit_code = exit_code
        super().__init__("Isolated native command failed")


# Fail with a public reason without including provider output or tokens
def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


# Reject worker-writable executable paths and follow only root-owned tool links
def trusted_file(path, digest=None):
    path = Path(path)
    resolved = path.resolve()
    for item in (path, *path.parents, resolved, *resolved.parents):
        info = item.lstat()
        require(info.st_uid == 0 and (item.is_symlink() or not info.st_mode & 0o022),
                "Untrusted guest executable ownership")
    require(path.is_file(), "Missing guest executable")
    if digest:
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "Guest executable digest changed")


# Bound each host message before parsing it
def message(stream):
    line = stream.readline(LIMIT + 1)
    require(line and len(line) <= LIMIT, "Missing or oversized host message")
    return json.loads(line)


# Keep protocol output separate from native tool diagnostics
def emit(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


# Stop descendants even when the native parent has already exited
def stop(process):
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=1)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


# Supervise one process group while host heartbeats remain current
def run(command, directory, environment, uid, gid, authority):
    authority()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
            stdout=output, stderr=errors, start_new_session=True, user=uid, group=gid, extra_groups=[])
        try:
            while process.poll() is None:
                authority()
                require(output.tell() + errors.tell() <= LIMIT, "Native output exceeded limit")
                time.sleep(0.1)
            if process.returncode != 0:
                raise NativeFailure(command[1], process.returncode)
            output.seek(0)
            result = output.read(LIMIT + 1)
            require(len(result) <= LIMIT, "Native output exceeded limit")
            authority()
            return result
        finally:
            stop(process)


# Verify the controller-owned backend after native initialization
def backend(directory, expected):
    data = json.loads((directory / ".terraform/terraform.tfstate").read_text())
    require(data.get("version") == 3 and data["backend"]["type"] == "gcs", "Unexpected backend format")
    values = data["backend"]["config"]
    require(all(values.get(key) == value for key, value in expected.items()), "Backend destination changed")
    require(not any(value for key, value in values.items() if key not in expected), "Backend override detected")
    workspace = directory / ".terraform/environment"
    require(not workspace.exists() or workspace.read_text().strip() == "default", "Unexpected native workspace")


# Prepare immutable source and a private writable native working directory
@contextlib.contextmanager
def staged(packet, account):
    with tempfile.TemporaryDirectory(prefix="deploy-ops-isolated-", dir="/var/tmp") as temporary:
        root = Path(temporary)
        root.chmod(0o711)
        source = root / "source"
        source.mkdir(mode=0o755)
        module = base64.b64decode(packet["module"], validate=True)
        lock = base64.b64decode(packet["lock"], validate=True)
        require(hashlib.sha256(lock).hexdigest() == packet["lock_digest"], "Provider lock changed")
        # The host compiler owns all source bytes; there are no user-selected archive paths
        (source / "main.tf.json").write_bytes(module)
        (source / ".terraform.lock.hcl").write_bytes(lock)
        for item in source.iterdir():
            item.chmod(0o444)
        home = root / "home"
        home.mkdir(mode=0o700)
        os.chown(home, account.pw_uid, account.pw_gid)
        data = root / "native"
        data.mkdir(mode=0o700)
        os.chown(data, account.pw_uid, account.pw_gid)
        cli = root / "terraform.rc"
        cli.write_text('provider_installation { filesystem_mirror { path = "/opt/deployops-tools/providers" '
                       'include = ["registry.opentofu.org/hashicorp/google"] } }\n')
        cli.chmod(0o444)
        plan = data / "approved.plan"
        if packet["stage"] == "apply":
            content = base64.b64decode(packet["plan"], validate=True)
            require(hashlib.sha256(content).hexdigest() == packet["artifact_digest"], "Saved plan changed")
            # Only the root helper can replace the approved plan before the native CLI opens it
            plan = root / "approved.plan"
            plan.write_bytes(content)
            plan.chmod(0o444)
        yield source, home, data, cli, plan


# Give every operation a new identity so earlier build processes cannot inspect its credentials
def worker_identity():
    occupied = set()
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            with contextlib.suppress(FileNotFoundError, ProcessLookupError):
                occupied.add(entry.stat().st_uid)
    for _ in range(100):
        uid = 200000 + secrets.randbelow(1000000)
        try:
            pwd.getpwuid(uid)
            continue
        except KeyError:
            pass
        try:
            grp.getgrgid(uid)
            continue
        except KeyError:
            pass
        if uid not in occupied:
            return SimpleNamespace(pw_uid=uid, pw_gid=uid)
    raise RuntimeError("Could not allocate an unused isolated worker identity")


# Refuse host-sharing and privileged workers before accepting a temporary token
def preflight(packet):
    require(sys.platform == "linux" and os.geteuid() == 0, "Use the installed root-owned Linux guest helper")
    trusted_file(Path(__file__), packet["helper_digest"])
    mounts = Path("/proc/self/mountinfo").read_text()
    require(" - virtiofs " not in mounts and " - 9p " not in mounts, "Host mounts are not allowed")
    account = worker_identity()
    require(packet["stage"] in {"plan", "apply"} and packet["engine"] in TOOLS, "Unknown isolated operation")
    module = json.loads(base64.b64decode(packet["module"], validate=True))
    bound = {"module": module, "lock_digest": packet["lock_digest"],
             "tool_digest": packet["tool_digest"], "provider_digest": packet["provider_digest"]}
    digest = hashlib.sha256(json.dumps(bound, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    require(digest == packet["packet_digest"], "Generated source packet changed")
    binary = "/opt/deployops-tools/" + TOOLS[packet["engine"]][0]
    trusted_file(binary, packet["tool_digest"])
    trusted_file(PROVIDER_FILE, packet["provider_digest"])
    require(time.time() < packet["deadline"] <= time.time() + 901, "Invalid guest deadline")
    return binary, account


# Accept host authority on one channel and terminate on silence or disconnection
def execute(packet, token, stream, binary, account):
    updates = queue.Queue()

    # A reader thread notices EOF while the native provider is blocked on a cloud request
    def receive():
        try:
            while True:
                value = message(stream)
                require(value == {"heartbeat": True}, "Unexpected heartbeat message")
                updates.put(time.monotonic())
        except Exception:
            updates.put(None)

    threading.Thread(target=receive, daemon=True).start()
    last_seen = time.monotonic()

    # Enforce both remote lease liveness and a guest-local absolute deadline
    def authority():
        nonlocal last_seen
        while not updates.empty():
            value = updates.get_nowait()
            require(value is not None, "Host authority channel closed")
            last_seen = value
        require(time.time() < packet["deadline"] and time.monotonic() - last_seen < 12,
                "Host authority expired")

    with staged(packet, account) as (source, home, data, cli, plan):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home),
               "TF_DATA_DIR": str(data / ".terraform"), "TF_CLI_CONFIG_FILE": str(cli),
               "TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "GOOGLE_OAUTH_ACCESS_TOKEN": token}
        native = lambda args: run([binary, *args], source, env, account.pw_uid, account.pw_gid, authority)
        version = json.loads(native(["version", "-json"]))["terraform_version"]
        require(version == TOOLS[packet["engine"]][1], "Native version changed")
        native(["init", "-input=false", "-lockfile=readonly", "-no-color", "-reconfigure"])
        backend(data, packet["backend"])
        if packet["stage"] == "plan":
            native(["plan", "-input=false", "-no-color", "-out=" + str(plan)])
            preview = json.loads(native(["show", "-json", str(plan)]))
            content = plan.read_bytes()
            require(len(content) <= 8_000_000, "Saved plan exceeded limit")
            return {"plan": base64.b64encode(content).decode(), "preview": preview,
                    "artifact_digest": hashlib.sha256(content).hexdigest()}
        native(["apply", "-input=false", "-no-color", str(plan)])
        backend(data, packet["backend"])
        state = native(["state", "pull"])
        return {"state": base64.b64encode(state).decode()}


# Serialize helpers while reserving ephemeral identities and holding temporary credentials
@contextlib.contextmanager
def exclusive():
    require(os.geteuid() == 0, "Guest supervisor requires root")
    descriptor = os.open("/run/deploy-ops-isolated.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        require(info.st_uid == 0 and not info.st_mode & 0o077, "Untrusted guest supervisor lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


# Expose only a fixed protocol; no shell commands or arbitrary filenames are accepted
def main():
    try:
        with exclusive():
            packet = message(sys.stdin.buffer)
            binary, account = preflight(packet)
            emit({"ready": True, "packet_digest": packet["packet_digest"]})
            secret = message(sys.stdin.buffer)
            require(set(secret) == {"token"} and isinstance(secret["token"], str)
                    and 20 <= len(secret["token"]) <= 16000 and not any(c.isspace() for c in secret["token"]),
                    "Invalid temporary credential")
            result = execute(packet, secret["token"], sys.stdin.buffer, binary, account)
            emit({"result": result})
    except NativeFailure as error:
        emit({"error": "Isolated native command failed", "stage": error.stage, "exit_code": error.exit_code})
        return 1
    except Exception:
        # Native details remain private; the controller records uncertainty for incomplete operations
        emit({"error": "Isolated native operation failed; inspect the target before retrying"})
        return 1
    return 0


# Keep imports usable for credentialless process supervision tests
if __name__ == "__main__":
    os._exit(main())
