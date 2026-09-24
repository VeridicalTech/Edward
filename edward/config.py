"""Policy packs for edward.

Zero-dependency config: TOML (stdlib tomllib) or JSON, inline presets.
A policy file may override any subset of fields; unknown keys produce a
warning (forward compatibility), bad value types raise PolicyError.
"""

import json
import os
import sys
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path


def _default_scorer_url() -> str:
    return os.environ.get("EDWARD_SCORER_URL", "http://localhost:8000")


def _default_scorer_backend() -> str:
    return os.environ.get("EDWARD_SCORER_BACKEND", "endpoint")


TRIGGER_DEFAULTS = {
    "error_rate": 0.4,
    "error_rate_window": 8,
    "retry_count": 3,
    "convergence_seconds": 600,
    "convergence_turns": 5,
    "passive_read_streak": 12,
    "unverified_write_streak": 10,
    "budget_pct": 0.8,
}

PRESETS = {
    "conservative": {
        "error_rate": 0.35, "error_rate_window": 8, "retry_count": 2,
        "convergence_seconds": 420, "convergence_turns": 4,
        "passive_read_streak": 10, "unverified_write_streak": 8,
        "budget_pct": 0.70,
    },
    "balanced": dict(TRIGGER_DEFAULTS),
    "aggressive": {
        "error_rate": 0.45, "error_rate_window": 10, "retry_count": 4,
        "convergence_seconds": 900, "convergence_turns": 8,
        "passive_read_streak": 16, "unverified_write_streak": 14,
        "budget_pct": 0.90,
    },
}

TRIGGER_TYPES = {
    "error_rate": float, "budget_pct": float,
    "error_rate_window": int, "retry_count": int,
    "convergence_seconds": int, "convergence_turns": int,
    "passive_read_streak": int, "unverified_write_streak": int,
}

KNOWN_TOP_KEYS = {
    "preset", "token_budget", "token_price_usd_per_1m", "allowed_paths",
    "session_dir", "triggers", "scorer", "notify", "intervention",
}
KNOWN_TOP_KEYS = {
    "preset", "token_budget", "token_price_usd_per_1m", "allowed_paths",
    "session_dir", "triggers", "scorer", "notify", "intervention", "receipts",
}
KNOWN_NESTED = {
    "scorer": {"base_url": "scorer_base_url", "enabled": "scorer_enabled",
               "timeout_seconds": "scorer_timeout_seconds",
               "backend": "scorer_backend"},
    "notify": {"webhook_url": "webhook_url", "stderr_banner": "stderr_banner"},
    "intervention": {"cooldown_seconds": "cooldown_seconds",
                     "auto_resume_seconds": "auto_resume_seconds",
                     "wait_approval_seconds": "wait_approval_seconds",
                     "approval_host": "approval_host",
                     "approval_port": "approval_port"},
    "receipts": {"enabled": "receipts_enabled"},
}


class PolicyError(ValueError):
    pass


@dataclass
class Policy:
    preset: str = "balanced"
    token_budget: int = 200_000
    token_price_usd_per_1m: float = 3.0
    allowed_paths: list = field(default_factory=list)
    session_dir: str = ""
    triggers: dict = field(default_factory=lambda: dict(TRIGGER_DEFAULTS))
    scorer_base_url: str = field(default_factory=_default_scorer_url)
    scorer_backend: str = field(default_factory=_default_scorer_backend)
    scorer_enabled: bool = True
    scorer_timeout_seconds: float = 10.0
    webhook_url: str = ""
    stderr_banner: bool = True
    cooldown_seconds: float = 10.0
    auto_resume_seconds: int = 0
    wait_approval_seconds: int = 0
    approval_host: str = "127.0.0.1"
    approval_port: int = 8765
    receipts_enabled: bool = True
    wait_approval_seconds: int = 0


def _warn(msg: str) -> None:
    print(f"[edward] config warning: {msg}", file=sys.stderr)


def _coerce_triggers(raw: dict, origin: str) -> dict:
    out = {}
    for key, value in raw.items():
        if key not in TRIGGER_TYPES:
            _warn(f"{origin}: unknown trigger '{key}' ignored")
            continue
        try:
            out[key] = TRIGGER_TYPES[key](value)
        except (TypeError, ValueError):
            raise PolicyError(f"{origin}: trigger '{key}' expects {TRIGGER_TYPES[key].__name__}, got {value!r}")
    for key in TRIGGER_TYPES:
        out.setdefault(key, TRIGGER_DEFAULTS[key])
    return out


def _apply_section(policy: Policy, section: str, raw: dict, origin: str) -> None:
    mapping = KNOWN_NESTED[section]
    for key, value in raw.items():
        if key not in mapping:
            _warn(f"{origin}: unknown key '{section}.{key}' ignored")
            continue
        target = mapping[key]
        current = getattr(policy, target)
        try:
            if isinstance(current, bool):
                setattr(policy, target, bool(value))
            elif isinstance(current, float):
                setattr(policy, target, float(value))
            elif isinstance(current, int):
                setattr(policy, target, int(value))
            else:
                setattr(policy, target, value)
        except (TypeError, ValueError):
            raise PolicyError(f"{origin}: '{section}.{key}' bad value {value!r}")


def load_policy(source: str = None) -> Policy:
    """source: None -> balanced preset; preset name; or path to .toml/.json."""
    policy = Policy()
    if source is None or source in PRESETS:
        if source:
            policy.preset = source
            policy.triggers = dict(PRESETS[source])
        return policy

    path = Path(source)
    if not path.exists():
        raise PolicyError(f"policy file not found: {source} (or a preset name: {', '.join(PRESETS)})")
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PolicyError(f"policy file {source} could not be parsed: {exc}")

    for key, value in data.items():
        if key == "triggers":
            policy.triggers = _coerce_triggers(value, source)
        elif key in ("scorer", "notify", "intervention"):
            if not isinstance(value, dict):
                raise PolicyError(f"{source}: [{key}] must be a table")
            _apply_section(policy, key, value, source)
        elif key in KNOWN_TOP_KEYS:
            if key == "preset":
                if value not in PRESETS:
                    raise PolicyError(f"{source}: preset must be one of {list(PRESETS)}")
                policy.preset = value
                base = dict(PRESETS[value])
                base.update({k: v for k, v in policy.triggers.items() if k in TRIGGER_DEFAULTS})
                policy.triggers = base
            elif key == "allowed_paths":
                if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
                    raise PolicyError(f"{source}: allowed_paths must be a list of strings")
                policy.allowed_paths = list(value)
            elif key == "token_budget":
                try:
                    policy.token_budget = int(value)
                except (TypeError, ValueError):
                    raise PolicyError(f"{source}: token_budget must be an integer, got {value!r}")
            elif key == "token_price_usd_per_1m":
                try:
                    policy.token_price_usd_per_1m = float(value)
                except (TypeError, ValueError):
                    raise PolicyError(f"{source}: token_price_usd_per_1m must be a number, got {value!r}")
            elif key == "session_dir":
                policy.session_dir = str(value)
        else:
            _warn(f"{source}: unknown key '{key}' ignored")

    if policy.token_budget <= 0:
        raise PolicyError(f"{source}: token_budget must be > 0")
    return policy


def policy_toml(policy: Policy) -> str:
    """Render a policy back to TOML (round-trip / template generation)."""
    lines = [
        f"preset = \"{policy.preset}\"",
        f"token_budget = {policy.token_budget}",
        f"token_price_usd_per_1m = {policy.token_price_usd_per_1m}",
        f"allowed_paths = {json.dumps(policy.allowed_paths)}",
        f"session_dir = \"{policy.session_dir}\"",
        "",
        "[triggers]",
    ]
    lines += [f"{k} = {v}" for k, v in policy.triggers.items()]
    lines += [
        "",
        "[scorer]",
        f"base_url = \"{policy.scorer_base_url}\"",
        f"backend = \"{policy.scorer_backend}\"",
        f"enabled = {str(policy.scorer_enabled).lower()}",
        f"timeout_seconds = {policy.scorer_timeout_seconds}",
        "",
        "[notify]",
        f"webhook_url = \"{policy.webhook_url}\"",
        f"stderr_banner = {str(policy.stderr_banner).lower()}",
        "",
        "[intervention]",
        f"cooldown_seconds = {policy.cooldown_seconds}",
        f"auto_resume_seconds = {policy.auto_resume_seconds}",
        "",
    ]
    return "\n".join(lines)


def with_overrides(policy: Policy, **kwargs) -> Policy:
    clean = {k: v for k, v in kwargs.items() if v is not None and k in {f.name for f in fields(Policy)}}
    return replace(policy, **clean)
