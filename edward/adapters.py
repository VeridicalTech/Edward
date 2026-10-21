"""Agent adapters: how Edward talks to each coding agent CLI.

An adapter knows three things about one agent family: whether a command
line belongs to it (matches), how to rebuild the command for controlled
execution (build_command), and how to recover the human prompt for display
(extract_prompt). Builtin: `pi` (native RPC mode) and `generic` (any
subprocess; JSONL auto-detect, wall-clock watchdog).

Third-party adapters plug in zero-dependency via importlib metadata:

    # pyproject.toml of an external package
    [project.entry-points."edward.adapters"]
    codex = "codex_edward:CodexAdapter"

Select with `edward wrap --agent <name> -- <command>`; `auto` (default)
picks `pi` when the command looks like pi, else generic.
"""

import json
import os
import shutil
import uuid
from typing import Optional


PI_VALUE_FLAGS = {"--provider", "--model", "--mode", "--session", "--session-id",
                  "--name", "--thinking", "--models", "--tools", "--exclude-tools",
                  "--session-dir", "--system-prompt", "--append-system-prompt"}


def _is_pi_rpc(cmd) -> bool:
    return bool(cmd) and os.path.basename(cmd[0]) == "pi"


def _extract_pi_prompt(cmd) -> str:
    parts = []
    skip_next = False
    for arg in cmd[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in PI_VALUE_FLAGS:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        parts.append(arg)
    return " ".join(parts)


def build_pi_command(cmd, session_id=None, ephemeral=False) -> list:
    base = list(cmd)
    if "--mode" not in base:
        base = [base[0], "--mode", "rpc"] + base[1:]
    if "--no-session" in base:
        base.remove("--no-session")
    if ephemeral:
        base.append("--no-session")
    elif session_id:
        base += ["--session-id", f"edward-{session_id}"]
    return base


class PiAdapter:
    name = "pi"

    @staticmethod
    def is_available() -> bool:
        return shutil.which("pi") is not None

    @staticmethod
    def matches(cmd) -> bool:
        return _is_pi_rpc(cmd)

    @staticmethod
    def build_command(cmd, session_id=None, ephemeral=False,
                      resume=False, feedback="") -> list:
        return build_pi_command(cmd, session_id=session_id, ephemeral=ephemeral)

    @staticmethod
    def extract_prompt(cmd) -> str:
        return _extract_pi_prompt(cmd)


class GenericAdapter:
    name = "generic"

    @staticmethod
    def is_available() -> bool:
        return True

    @staticmethod
    def matches(cmd) -> bool:
        return True

    @staticmethod
    def build_command(cmd, session_id=None, ephemeral=False,
                      resume=False, feedback="") -> list:
        return list(cmd)

    @staticmethod
    def extract_prompt(cmd) -> str:
        return " ".join(cmd)


# --- claude code / codex: stream -> canonical event translation ------------
#
# Both CLIs print JSONL on stdout in print/exec modes. Adapters translate
# each line into zero or more canonical (pi-schema) events so the universal
# control-plane loop can supervise them unchanged:
#   claude -p <task> --output-format stream-json --verbose
#   codex exec --json <task>
# Verified against live CLI output (claude 2.1.167, codex 0.2x) and on-disk
# session rollouts.

_CLAUDE_TOOL_MAP = {
    "Bash": "bash", "Write": "write", "Edit": "edit", "MultiEdit": "edit",
    "NotebookEdit": "edit", "Read": "read", "Grep": "grep", "Glob": "grep",
    "WebFetch": "browse", "WebSearch": "browse",
}
_CODEX_EXEC_MAP = {
    "exec_command": "bash", "shell": "bash", "terminal": "bash",
    "apply_patch": "edit", "edit_file": "edit", "write_file": "write",
}


def _canon_tool_event(tool, args, is_error=False):
    return {"type": "tool_execution_end", "toolName": tool,
            "args": args or {}, "isError": bool(is_error)}


def _supervisor_note(feedback: str) -> str:
    note = ("[edward supervisor] This session was paused by the external "
            "control plane")
    if feedback:
        note += f": {feedback}"
    return (note + ". Fix the reported condition, then continue the task "
            "without repeating the paused step.")


class ClaudeAdapter:
    """claude -p --output-format stream-json --verbose supervision.

    Resume parity: new sessions get a deterministic UUID (--session-id) so
    `edward wrap --continue` can resume the exact conversation via
    `--resume <uuid>` with a supervisor note appended to the prompt."""

    name = "claude"

    @staticmethod
    def is_available() -> bool:
        return shutil.which("claude") is not None

    @staticmethod
    def matches(cmd) -> bool:
        return bool(cmd) and os.path.basename(cmd[0]) == "claude"

    @staticmethod
    def session_uuid(session_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"edward-{session_id}"))

    @staticmethod
    def build_command(cmd, session_id=None, ephemeral=False,
                      resume=False, feedback="") -> list:
        out = list(cmd)
        if "-p" not in out:
            for i, a in enumerate(out[1:], 1):
                if not a.startswith("-"):
                    out.insert(i, "-p")
                    break
        if "--output-format" not in out:
            out += ["--output-format", "stream-json"]
        if "--verbose" not in out:
            out += ["--verbose"]  # stream-json refuses to work without it
        suid = ClaudeAdapter.session_uuid(session_id) if session_id else None
        if resume and suid:
            if "--session-id" in out:
                i = out.index("--session-id")
                out[i] = "--resume"
                out[i + 1] = suid
            elif "--resume" not in out:
                i = out.index("-p")
                out[i:i] = ["--resume", suid]
            for j, a in enumerate(out):
                if a == "-p" and j + 1 < len(out):
                    out[j + 1] = f"{out[j + 1]}\n\n{_supervisor_note(feedback)}"
                    break
        elif suid and "--session-id" not in out and "--resume" not in out:
            out.insert(out.index("-p"), "--session-id")
            out.insert(out.index("--session-id") + 1, suid)
        return out

    @staticmethod
    def extract_prompt(cmd) -> str:
        for i, a in enumerate(cmd[1:], 1):
            if a == "-p" and i + 1 < len(cmd):
                return cmd[i + 1]
            if not a.startswith("-") and a != "-p":
                return a
        return " ".join(cmd)

    @staticmethod
    def parse_line(line):
        try:
            j = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(j, dict):
            return []
        t = j.get("type", "")
        if t == "system":
            if j.get("subtype") == "init":
                return [{"type": "agent_start"}]
            if j.get("subtype") == "api_retry":
                return [{"type": "auto_retry_start"}]
            return []
        if t == "assistant":
            events = []
            for block in (j.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                raw = block.get("input") or {}
                args = {"path": raw.get("file_path") or raw.get("path") or "",
                        "command": raw.get("command") or ""}
                tool = _CLAUDE_TOOL_MAP.get(block.get("name", ""),
                                            (block.get("name") or "other").lower())
                events.append(_canon_tool_event(tool, args))
            return events
        if t == "result":
            return [{"type": "agent_end"}]
        return []


class CodexAdapter:
    """codex exec --json supervision (live stream + on-disk rollouts).

    Resume parity: `codex exec resume --last` continues the most recent
    recorded thread with a supervisor note appended to the prompt. Caveat:
    --last assumes the paused codex session is the most recent one (codex
    assigns thread ids server-side; there is no pin-at-create flag)."""

    name = "codex"

    @staticmethod
    def is_available() -> bool:
        return shutil.which("codex") is not None

    @staticmethod
    def matches(cmd) -> bool:
        return bool(cmd) and os.path.basename(cmd[0]) == "codex"

    @staticmethod
    def build_command(cmd, session_id=None, ephemeral=False,
                      resume=False, feedback="") -> list:
        out = list(cmd)
        if len(out) > 1 and out[1] not in ("exec", "resume"):
            out.insert(1, "exec")
        if "exec" in out[1:2] and "--json" not in out:
            out.insert(2, "--json")
        if resume:
            if "resume" not in out[2:3]:
                out.insert(2, "resume")
            if "--last" not in out:
                out.insert(3, "--last")
            note = _supervisor_note(feedback)
            prompt_idx = max((i for i, a in enumerate(out)
                              if not a.startswith("-") and a != "resume"
                              and i >= 2 and a != "--json" and a != "--last"),
                             default=None)
            if prompt_idx is not None:
                out[prompt_idx] = f"{out[prompt_idx]}\n\n{note}"
            else:
                out.append(note)
        return out

    @staticmethod
    def extract_prompt(cmd) -> str:
        parts, skip = [], False
        for i, a in enumerate(cmd[1:], 1):
            if skip:
                skip = False
                continue
            if i == 1 and a == "exec":
                continue
            if a in ("--json", "-c", "--model", "--sandbox", "--cd", "--profile"):
                skip = a in ("-c", "--model", "--sandbox", "--cd", "--profile")
                continue
            if a.startswith("-"):
                continue
            parts.append(a)
        return " ".join(parts)

    @staticmethod
    def parse_line(line):
        try:
            j = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(j, dict):
            return []
        t = j.get("type", "")
        if t == "thread.started":
            return [{"type": "agent_start"}]
        if t == "turn.started":
            return [{"type": "turn_start"}]
        if t == "response_item":  # on-disk rollout schema
            p = j.get("payload") or {}
            if p.get("type") != "function_call":
                return []
            try:
                args_raw = json.loads(p.get("arguments") or "{}")
            except (json.JSONDecodeError, ValueError):
                args_raw = {}
            tool = _CODEX_EXEC_MAP.get(p.get("name", ""), "other")
            if tool == "bash":
                cmd_arg = args_raw.get("cmd") or args_raw.get("command") or ""
                if isinstance(cmd_arg, list):
                    cmd_arg = " ".join(str(c) for c in cmd_arg)
                return [_canon_tool_event("bash", {"command": str(cmd_arg)[:2000]})]
            return [_canon_tool_event(tool, {"path": args_raw.get("file_path") or ""})]
        if t in ("item.started", "item.completed", "item.updated"):
            item = j.get("item") or {}
            it = item.get("type", "")
            if it == "command_execution":
                cmd_arg = item.get("command") or ""
                if isinstance(cmd_arg, list):
                    cmd_arg = " ".join(str(c) for c in cmd_arg)
                err = item.get("exit_code") not in (0, None) \
                    or item.get("status") in ("failed", "error")
                return [_canon_tool_event("bash", {"command": str(cmd_arg)[:2000]}, err)]
            if it == "file_change":
                paths = [c.get("path", "") for c in item.get("changes") or []
                         if isinstance(c, dict)]
                err = item.get("status") in ("failed", "error")
                return [_canon_tool_event("edit", {"path": "; ".join(paths)[:400]}, err)]
            if it == "mcp_tool_call":
                err = item.get("status") in ("failed", "error")
                return [_canon_tool_event(str(item.get("tool", "other")), {}, err)]
            if it == "error":
                return [{"type": "auto_retry_start"}]
        return []


BUILTIN_ADAPTERS = {"pi": PiAdapter, "generic": GenericAdapter,
                    "claude": ClaudeAdapter, "codex": CodexAdapter}


def _entry_point_adapters() -> dict:
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        if hasattr(eps, "select"):
            selected = eps.select(group="edward.adapters")
        else:  # pragma: no cover - py3.9 style
            selected = eps.get("edward.adapters", [])
        return {ep.name: ep.load() for ep in selected}
    except Exception:
        return {}


def available_adapters() -> list:
    return sorted(set(BUILTIN_ADAPTERS) | set(_entry_point_adapters()))


def resolve_adapter(name: str = "auto", cmd=None):
    """Return an adapter class, or None for the generic (default) path.

    `auto` picks pi only when the command line looks like pi. Unknown
    names exit with the list of what exists (builtin + entry points).
    """
    name = (name or "auto").strip().lower()
    if name in ("", "auto"):
        if PiAdapter.matches(cmd or []) and PiAdapter.is_available():
            return PiAdapter
        return None
    if name == "generic":
        return None
    if name in BUILTIN_ADAPTERS:
        return BUILTIN_ADAPTERS[name]
    adapters = _entry_point_adapters()
    if name in adapters:
        return adapters[name]
    raise SystemExit(
        f"unknown --agent {name!r}; available: {', '.join(available_adapters())}")
