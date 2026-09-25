"""Cross-platform child-process management for interventions.

wrap's interventions (PAUSE/CANCEL/BLOCK) must terminate the whole agent
process tree, not just the direct child — agents shell out (npm, python,
git hooks) and killing only the child orphans them.

- spawn(): starts the child in its own process group (POSIX setsid,
  Windows CREATE_NEW_PROCESS_GROUP) so the group can be signalled.
- terminate_tree(): graceful-then-forced escalation. POSIX: killpg
  SIGTERM → SIGKILL. Windows: CTRL_BREAK_EVENT to the group → taskkill /T /F.
"""

import os
import signal
import subprocess
import sys


def spawn(cmd, **kwargs) -> subprocess.Popen:
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs.setdefault("start_new_session", True)
    return subprocess.Popen(cmd, **kwargs)


def terminate_tree(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """Terminate the full process tree behind `proc`, escalating to force."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        _terminate_windows(proc, grace)
    else:
        _terminate_posix(proc, grace)


def _terminate_windows(proc: subprocess.Popen, grace: float) -> None:
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT)  # delivered to the group
    except (OSError, ValueError, AttributeError):
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                   capture_output=True, check=False)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def _terminate_posix(proc: subprocess.Popen, grace: float) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
