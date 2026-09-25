"""ControlPlane: the reusable monitoring core.

Wires StateEngine + triggers + scorer + audit + notify. Knows nothing
about processes: it consumes events (pi-native, or any runtime via the
canonical schema), returns Decisions, and records evidence. The caller
(wrap CLI, demo, eval, tests) decides how to execute an intervention.
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .audit import AuditLog
from .canonical_events import normalize_event
from .kernel import ControlKernel, DecisionAuthority
from .config import Policy  # noqa: F401  (typing only)
from .scorer import Scorer
from .state_engine import StateEngine
from .triggers import TriggerResult, check_triggers
from .notify import notify_stderr, notify_webhook


@dataclass
class Decision:
    action: str
    source: str
    authority: str
    decision_type: str
    reason: str
    trigger: TriggerResult
    jev: Optional[dict] = None


RESUMABLE_ACTIONS = {"PAUSE"}
TERMINAL_ACTIONS = {"CANCEL", "ESCALATE", "BLOCK", "REQUEST_HUMAN_APPROVAL"}


class ControlPlane:
    def __init__(self, policy, session: str = "", audit: Optional[AuditLog] = None,
                 scorer: Optional[Scorer] = None, log: Optional[Callable[[str], None]] = None,
                 clock: Optional[Callable[[], float]] = None):
        self.policy = policy
        self.session = session
        self.audit = audit
        self.scorer = scorer
        self.log = log or (lambda msg: None)
        self.clock = clock or time.time
        self.state_engine = StateEngine(token_budget=policy.token_budget,
                                        clock=self.clock if clock else None)
        if policy.allowed_paths:
            self.state_engine.state.allowed_paths = list(policy.allowed_paths)
        self._last_fired = 0.0
        self.interventions = 0

    @property
    def state(self):
        return self.state_engine.state

    def process_event(self, raw: dict, source: str = "pi") -> Optional[Decision]:
        try:
            if source == "pi":
                event = raw
            else:
                event = normalize_event(raw, source).to_dict()
            self.state_engine.process_event(event)
        except Exception as exc:
            self.log(f"event ingestion error (ignored): {exc}")
            return None
        return self.check()

    def check(self) -> Optional[Decision]:
        now = self.clock()
        if now - self._last_fired < self.policy.cooldown_seconds:
            return None

        try:
            trigger = check_triggers(self.state_engine.state, self.policy.triggers)
        except Exception as exc:
            self.log(f"trigger evaluation error (ignored): {exc}")
            return None
        if not trigger:
            return None

        self._last_fired = now
        mss = self.state_engine.state.to_mss()

        if trigger.decision_type == "request_permission":
            decision = Decision(
                action="REQUEST_HUMAN_APPROVAL", source="hard_constraint",
                authority="hard_constraint", decision_type=trigger.decision_type,
                reason=trigger.reason, trigger=trigger,
            )
        else:
            jev_result = None
            if self.scorer is not None:
                try:
                    jev_result = self.scorer.consult(mss, trigger.reason)
                except Exception as exc:
                    self.log(f"scorer error (degrading to rule): {exc}")
                    jev_result = None
            if self.scorer is not None and (jev_result is None or "choice" not in jev_result):
                # Scorer consulted but produced no usable verdict: the rule
                # action stands, and the audit trail records the unresolved
                # consultation instead of silently dropping it.
                jev_result = {"unresolved": "scorer_unavailable"}
            decision_dict = {
                "rule_action": "PAUSE",
                "jev_action": (jev_result or {}).get("choice"),
                "jev_confidence": (jev_result or {}).get("confidence", 0.0),
                "authority": DecisionAuthority.SOFT_DECISION,
            }
            action, source = ControlKernel.resolve_action(decision_dict)
            decision = Decision(
                action=action, source=source, authority="soft_decision",
                decision_type=trigger.decision_type, reason=trigger.reason,
                trigger=trigger, jev=jev_result,
            )

        self.interventions += 1
        if self.audit:
            est_avoided_usd = None
            if decision.decision_type == "should_continue" and self.policy.token_budget:
                remaining = max(self.policy.token_budget - mss.get("token_usage", 0), 0)
                est_avoided_usd = round(
                    remaining / 1_000_000 * self.policy.token_price_usd_per_1m, 4)
            self.audit.intervention(
                session=self.session, trigger_reason=trigger.reason,
                decision_type=decision.decision_type, action=decision.action,
                source=decision.source, authority=decision.authority,
                mss=mss, jev=decision.jev or {},
                est_avoided_usd=est_avoided_usd,
            )
        self._notify(decision)
        return decision

    def _notify(self, decision: Decision) -> None:
        if self.policy.stderr_banner:
            hint = ""
            if decision.action in RESUMABLE_ACTIONS and self.policy.auto_resume_seconds == 0:
                hint = "Paused. Resume manually: edward wrap --continue -- <command>"
            elif decision.action in TERMINAL_ACTIONS:
                hint = "Session terminated by control plane (not resumable)."
            notify_stderr(decision.action, decision.reason, hint)
        if self.policy.webhook_url:
            text = f"[edward] {decision.action}: {decision.reason} (source: {decision.source})"
            if not notify_webhook(self.policy.webhook_url, text):
                self.log("webhook notification failed (ignored)")

    def summary(self) -> dict:
        s = self.state
        return {
            "turn_count": s.turn_count,
            "total_tool_calls": s.total_tool_calls,
            "token_usage": s.token_usage,
            "cost_usd": round(s.cost_usd, 4),
            "files_modified": len(s.files_modified),
            "interventions": self.interventions,
        }
