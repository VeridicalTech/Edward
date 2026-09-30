"""Semantic scorer client with a circuit breaker.

Consults a JudgmentBackend (see backends.py): endpoint (LAN /v1/score),
Jev (batched /v1/systemone), or heuristic (offline stub). After 2
consecutive failures the breaker opens for 60s: calls return None
immediately and the control plane runs rule-only. Breaker recovers on
first success. The scorer is always advisory — every None path degrades
to the deterministic rule action.
"""

import time
from typing import Optional

from .backends import EndpointBackend, JudgmentBackend, backend_from_env
from .scorer_client import CONTINUE_OPTIONS, SCORER_API_URL


BREAKER_THRESHOLD = 2
BREAKER_COOLDOWN_SECONDS = 60.0


def make_scorer(policy, post_fn=None) -> "Scorer":
    """Build the scorer from policy + environment (EDWARD_SCORER_BACKEND)."""
    backend, _ = backend_from_env(
        policy.scorer_base_url, policy.scorer_timeout_seconds, post_fn=post_fn
    )
    return Scorer(timeout=policy.scorer_timeout_seconds, backend=backend)


class Scorer:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0,
                 backend: Optional[JudgmentBackend] = None):
        if backend is None:
            backend = EndpointBackend(base_url or SCORER_API_URL, timeout=timeout)
        self.backend = backend
        self.fail_streak = 0
        self.open_until = 0.0
        self.total_calls = 0
        self.total_failures = 0

    @property
    def name(self) -> str:
        return self.backend.name

    def health(self):
        return self.backend.health()

    def consult(self, mss: dict, trigger_reason: str = ""):
        question = "Based on the agent state, should the agent continue executing its current task?"
        if trigger_reason:
            question = f"The external control plane fired a deterministic trigger: {trigger_reason}. Given this and the agent state, should the agent continue?"
        return self._guarded(lambda: self.backend.ask(mss, question, CONTINUE_OPTIONS))

    def consult_pareto(self, mss: dict, trigger_reason: str = ""):
        """Multi-candidate evaluation in one wire call (JevTree's Pareto idea):
        recovery probability per candidate action + risk of inaction. The
        kernel composes the final action deterministically — the model only
        supplies calibrated probabilities. Returns None on failure."""
        ctx = (f" The control plane fired a deterministic trigger: {trigger_reason}."
               if trigger_reason else "")
        state = dict(mss or {})
        state["decision_context"] = ("An AI coding agent trajectory is being "
                                     "supervised by an external control plane." + ctx)
        yn = {"yes": "yes", "no": "no"}
        questions = {
            "recover_continue": ("If the supervisor takes no action, what is the probability the agent converges without further waste or harm?", yn),
            "recover_pause": ("If the supervisor pauses the agent now for review, what is the probability the overall task still converges afterwards?", yn),
            "recover_cancel": ("If the supervisor cancels the agent now, what is the probability the overall task still converges or is already beyond saving?", yn),
            "risk_inaction": ("If the supervisor takes no action, what is the probability that waste or harm continues or escalates?", yn),
        }
        return self._guarded(lambda: self._pareto_impl(state, questions))

    def consult_future(self, mss: dict, trigger_reason: str = ""):
        """Receding-horizon probe: if the supervisor waits one horizon without
        intervening, will the agent make progress, and will harm escalate?
        Returns {"converge_next": p, "harm_next": p, "backend": name} or None.
        Powers the WAIT action (prediction-gated deferral)."""
        ctx = (f" The control plane fired a deterministic trigger: {trigger_reason}."
               if trigger_reason else "")
        state = dict(mss or {})
        state["decision_context"] = ("An AI coding agent trajectory is being "
                                     "supervised by an external control plane." + ctx)
        yn = {"yes": "yes", "no": "no"}
        questions = {
            "converge_next": ("If the supervisor waits one more monitoring horizon without intervening, what is the probability the agent makes measurable progress toward its task in that time?", yn),
            "harm_next": ("If the supervisor waits one more monitoring horizon without intervening, what is the probability that waste escalates or irreversible harm occurs in that time?", yn),
        }
        return self._guarded(lambda: self._future_impl(state, questions))

    def _future_impl(self, state, questions):
        results = self.backend.ask_many(state, questions)
        def p(qid):
            r = results.get(qid)
            if not r:
                return None
            probs = r.get("probabilities") or {}
            if "yes" in probs:
                return probs["yes"]
            return r.get("confidence")
        c, h = p("converge_next"), p("harm_next")
        if c is None or h is None:
            return None
        return {"converge_next": c, "harm_next": h, "backend": self.name}

    def _pareto_impl(self, state, questions):
        results = self.backend.ask_many(state, questions)
        def p(qid):
            r = results.get(qid)
            if not r:
                return None
            probs = r.get("probabilities") or {}
            if "yes" in probs:
                return probs["yes"]
            return r.get("confidence")
        rec = {a: p(f"recover_{a}") for a in ("continue", "pause", "cancel")}
        risk = p("risk_inaction")
        if any(v is None for v in rec.values()) or risk is None:
            return None
        return {"recover": rec, "risk_inaction": risk, "backend": self.name}

    def _guarded(self, fn):
        now = time.time()
        if now < self.open_until:
            return None
        self.total_calls += 1
        try:
            result = fn()
        except Exception:
            result = None
        if result is None:
            self.total_failures += 1
            self.fail_streak += 1
            if self.fail_streak >= BREAKER_THRESHOLD:
                self.open_until = now + BREAKER_COOLDOWN_SECONDS
            return None
        self.fail_streak = 0
        self.open_until = 0.0
        return result
