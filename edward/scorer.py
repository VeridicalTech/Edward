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
        return self._guarded(mss, question, CONTINUE_OPTIONS)

    def _guarded(self, state, question: str, options: dict):
        now = time.time()
        if now < self.open_until:
            return None
        self.total_calls += 1
        try:
            result = self.backend.ask(state, question, options)
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
