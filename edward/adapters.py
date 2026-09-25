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
    def build_command(cmd, session_id=None, ephemeral=False) -> list:
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
    def build_command(cmd, session_id=None, ephemeral=False) -> list:
        return list(cmd)

    @staticmethod
    def extract_prompt(cmd) -> str:
        return " ".join(cmd)


BUILTIN_ADAPTERS = {"pi": PiAdapter, "generic": GenericAdapter}


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
