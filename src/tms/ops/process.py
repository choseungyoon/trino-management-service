"""Running a long external command and showing it happening.

Extracted from the restart executor so fleet jobs (FR-FL-04) get the same
behaviour rather than a second, subtly different copy. Everything here was
learned from the restart path:

* **Popen, not `subprocess.run`.** Capturing output only returns it once the
  process has exited, and these run for minutes. A screen that shows nothing
  until the work is over cannot distinguish "connecting to the first host" from
  "hung", which is exactly the moment someone needs to know.
* **A watchdog, not `run(timeout=...)`.** A timeout argument cannot interrupt a
  read loop. A playbook that hangs has to fail the step rather than hold a
  cluster in a half-finished state indefinitely.
* **Redaction on every line**, before it reaches a caller that might persist it.

Python 3.9 compatible.
"""

import logging
import re
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# ⛔ The leading `[\w-]*` matters. Ansible's secrets are rarely bare words, and
# `\bpassword\b` misses `vault_password` and `become_password` - there is no
# word boundary inside them.
#
# `pass` is pinned to the end of a word (`ansible_ssh_pass`); letting it take a
# suffix too would redact `passed: 3`.
_REDACT = re.compile(
    r"(?i)\b([\w-]*(?:password|passwd|secret|token|api[_-]?key)[\w-]*|[\w-]*pass)\b"
    r"(\s*[:=]\s*)(\S+)")


def redact(text: str) -> str:
    """Mask obvious `key: value` secrets in captured output.

    Best effort only - this output is not structured and the pattern cannot be
    exhaustive. The log is treated as sensitive regardless of what this catches.
    """
    return _REDACT.sub(lambda m: m.group(1) + m.group(2) + "***", text or "")


def stream_command(command: List[str], timeout: float,
                   on_line: Callable[[str], None],
                   env: Optional[Dict[str, str]] = None,
                   cwd: Optional[str] = None) -> Dict[str, Any]:
    """Run `command`, handing each line to `on_line` as it is produced.

    Returns `{"rc": int}`, `{"rc": None, "error": str}` when the process could
    not be started, or `{"rc": None, "timed_out": True}`. The caller decides
    what those mean - this does not know whether a non-zero exit is a failure or
    a documented outcome.

    Never a shell. `command` is an argument list, so nothing in it can be
    reinterpreted as syntax however it was assembled.
    """
    try:
        process = subprocess.Popen(  # noqa: S603 - list form, no shell
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1, shell=False,
            env=env, cwd=cwd,
        )
    except OSError as exc:
        return {"rc": None, "error": str(exc)}

    expired = threading.Event()

    def give_up() -> None:
        expired.set()
        try:
            process.kill()
        except OSError:  # pragma: no cover - already gone
            pass

    watchdog = threading.Timer(timeout, give_up)
    watchdog.daemon = True
    watchdog.start()
    try:
        for raw in process.stdout:
            line = redact(raw.rstrip("\r\n"))
            if line.strip():
                on_line(line)
        returncode = process.wait()
    finally:
        watchdog.cancel()
        try:
            process.stdout.close()
        except Exception:  # noqa: BLE001 - pragma: no cover
            pass

    if expired.is_set():
        return {"rc": None, "timed_out": True}
    return {"rc": returncode}
