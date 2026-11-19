# src/deploy_ops/delivery/process.py - Run bounded argument lists without inheriting personal credentials.


import os
from contextlib import suppress
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from ..errors import Refused

# Ambient credentials and arbitrary endpoint overrides are excluded from child environments
SAFE_ENV = {"PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SYSTEMROOT", "WINDIR"}


# Fixed operation names identify native failures without recording arguments or provider errors
AZURE_OPERATIONS = (
    ("cloud", "set"), ("login",), ("account", "set"), ("account", "show"),
    ("group", "show"), ("version",), ("deployment", "group", "what-if"),
    ("deployment", "group", "list"), ("deployment", "group", "create"),
    ("deployment", "group", "show"), ("containerapp", "show"),
    ("containerapp", "update"), ("containerapp", "revision", "show"),
)


# Only known command prefixes contribute labels to public execution events
def command_metadata(argv: list[str]) -> dict[str, str]:
    result = {"tool": Path(argv[0]).name}
    if result["tool"] == "az":
        for operation in AZURE_OPERATIONS:
            if tuple(argv[1:1 + len(operation)]) == operation:
                result["operation"] = "azure." + ".".join(operation)
                break
    return result


# Start from a small host allowlist and add only explicit credential references
def environment(home: Path, credentials: dict[str, str] | None = None) -> dict[str, str]:
    values = {key: value for key, value in os.environ.items() if key in SAFE_ENV}
    values.update(HOME=str(home), TMPDIR=str(home), TF_IN_AUTOMATION="1", TF_INPUT="0",
                  CLOUDSDK_CORE_DISABLE_PROMPTS="1", AZURE_CORE_NO_COLOR="true")
    values.update(credentials or {})
    return values


# Remove known secrets and common credential assignments from displayed output
def redact(text: str, secrets: list[str]) -> str:
    ranges = []
    for secret in set(item for item in secrets if len(item) >= 8):
        start = 0
        while (position := text.find(secret, start)) >= 0:
            end = position + len(secret)
            # Collapse dense self-overlap while retaining matches from every registered value
            if ranges and ranges[-1][0] <= position <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
            else:
                ranges.append((position, end))
            start = position + 1
    for match in re.finditer(r"(?i)(token|password|secret|authorization)(\s*[:=]\s*)([^\s,]+)", text):
        ranges.append(match.span(3))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts, previous = [], 0
    for start, end in merged:
        parts.extend((text[previous:start], '[redacted]'))
        previous = end
    return ''.join(parts) + text[previous:]


# Supervise bounded native commands with authority heartbeats
class Process:
    # Keep process credentials and deadlines local to this execution context
    def __init__(self, home: Path, emit, credentials: dict[str, str] | None = None,
                 heartbeat=None, deadline: int | None = None):
        self.env = environment(home, credentials)
        self.emit = emit
        self.heartbeat = heartbeat or (lambda: None)
        self.deadline = deadline
        self.secrets = [value for key, value in (credentials or {}).items()
                        if any(word in key for word in ("TOKEN", "SECRET", "KEY", "PASSWORD"))]


    # Capture provider output privately while supervising the whole process group
    def run(self, argv: list[str], cwd: Path, timeout: int = 600, capture: bool = False,
            input_text: str | None = None) -> str:
        """Poll authority while commands run. Provider-side work may outlive cancellation."""
        if not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
            raise Refused("Invalid command argument list", 422)
        self.heartbeat()
        metadata = command_metadata(argv)
        self.emit("command.started", metadata)

        # Private streams keep raw provider responses out of ordinary command events
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostic, tempfile.TemporaryFile() as stdin:
            if input_text is not None:
                stdin.write(input_text.encode())
                stdin.seek(0)
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    argv, cwd=cwd, env=self.env, stdin=stdin,
                    stdout=output, stderr=diagnostic, start_new_session=os.name == "posix",
                )
            except OSError:
                # A launch refusal is not an attempted provider operation; keep host paths private
                self.emit("command.failed", {**metadata, "elapsed_ms": round((time.monotonic() - started) * 1000),
                          "outcome": "not-started", "exit_code": None})
                label = metadata.get("operation", metadata["tool"])
                raise Refused(f"{label} could not start; check the local executable and working directory") from None
            try:
                # Authority expiry and output limits interrupt local execution without claiming rollback
                while process.poll() is None:
                    if time.monotonic() - started >= timeout or (self.deadline and time.time() >= self.deadline):
                        raise Refused("Command deadline reached. Check the target before retrying")
                    if output.tell() + diagnostic.tell() > 10_000_000:
                        raise Refused("Command output exceeded its 10 MB limit")
                    self.heartbeat()
                    time.sleep(0.25)

                # Only bounded successful output can reach the caller
                output.seek(0)
                text = output.read(10_000_001).decode(errors="replace")
                if len(text.encode()) > 10_000_000:
                    raise Refused("Command output exceeded its 10 MB limit")
                if process.returncode != 0:
                    # Raw provider output can contain secrets that the controller never received
                    label = metadata.get("operation", metadata["tool"])
                    raise Refused(f"{label} failed with exit code {process.returncode}")
                if not capture and text:
                    # Keep complete masking context before selecting the bounded display tail
                    self.emit("command.output", {"text": redact(text, self.secrets)[-4000:]})
                self.emit("command.finished", {**metadata, "elapsed_ms": round((time.monotonic() - started) * 1000)})
                return text
            except Exception:
                # Record local interruption without exposing diagnostics or implying provider rollback
                self.emit("command.failed", {**metadata, "exit_code": process.poll(),
                          "elapsed_ms": round((time.monotonic() - started) * 1000),
                          "outcome": "failed" if process.returncode is not None else "interrupted"})
                raise
            finally:
                if os.name == "posix":
                    # Descendants may survive a parent exit and ignore graceful termination
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=3)
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                elif process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
