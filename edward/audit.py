"""Append-only JSONL audit log.

Never blocks or crashes the control plane: if the disk write fails, the
entry is echoed to stderr once and monitoring continues. Rotates at 50 MB
by renaming to <name>.1.
"""

import json
import os
import sys
import time
from pathlib import Path


MAX_BYTES = 50 * 1024 * 1024
SCHEMA_VERSION = 1


class AuditLog:
    def __init__(self, path=None, receipts=None):
        self.path = Path(path) if path else None
        self.receipts = receipts
        self._degraded = False
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._degrade(f"cannot create audit dir {self.path.parent}: {exc}")

    def _degrade(self, msg: str) -> None:
        if not self._degraded:
            self._degraded = True
            print(f"[edward] audit degraded to stderr: {msg}", file=sys.stderr)

    def emit(self, entry_type: str, session: str = "", **fields) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "v": SCHEMA_VERSION,
            "type": entry_type,
            "session": session,
        }
        record.update(fields)
        line = json.dumps(record, default=str, ensure_ascii=False)
        if self.path is None:
            return
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                rotated = self.path.with_suffix(self.path.suffix + ".1")
                os.replace(self.path, rotated)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            self._degrade(f"{exc}")
            print(f"[edward] audit: {line}", file=sys.stderr)
            return
        if self.receipts is not None:
            try:
                self.receipts.append(record)
            except OSError as exc:
                self._degrade(f"receipt chain: {exc}")

    def session_start(self, session: str, policy_preset: str, command) -> None:
        self.emit("session_start", session=session, policy_preset=policy_preset,
                  command=[str(c) for c in command])

    def intervention(self, session: str, trigger_reason: str, decision_type: str,
                     action: str, source: str, authority: str, mss: dict,
                     jev: dict = None, est_avoided_usd: float = None) -> None:
        self.emit("intervention", session=session, trigger_reason=trigger_reason,
                  decision_type=decision_type, action=action, source=source,
                  authority=authority, mss=mss, jev=jev or {},
                  est_avoided_usd=est_avoided_usd)

    def session_end(self, session: str, reason: str, exit_code: int) -> None:
        self.emit("session_end", session=session, reason=reason, exit_code=exit_code)


def summarize(path) -> dict:
    """Read an audit file and aggregate the numbers that matter."""
    path = Path(path)
    summary = {
        "file": str(path), "sessions": 0, "interventions": 0,
        "by_action": {}, "by_decision_type": {}, "tokens_at_intervention": [],
        "cost_usd_at_intervention": [], "est_avoided_usd": 0.0,
        "first_ts": None, "last_ts": None,
    }
    sessions = set()
    if not path.exists():
        return summary
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts")
            if ts:
                summary["first_ts"] = summary["first_ts"] or ts
                summary["last_ts"] = ts
            if rec.get("session"):
                sessions.add(rec["session"])
            if rec.get("type") == "intervention":
                summary["interventions"] += 1
                action = rec.get("action", "?")
                summary["by_action"][action] = summary["by_action"].get(action, 0) + 1
                dtype = rec.get("decision_type", "?")
                summary["by_decision_type"][dtype] = summary["by_decision_type"].get(dtype, 0) + 1
                mss = rec.get("mss") or {}
                if mss.get("token_usage"):
                    summary["tokens_at_intervention"].append(mss["token_usage"])
                if mss.get("cost_usd"):
                    summary["cost_usd_at_intervention"].append(mss["cost_usd"])
                if rec.get("est_avoided_usd") is not None:
                    summary["est_avoided_usd"] += rec["est_avoided_usd"]
    summary["sessions"] = len(sessions)
    return summary
