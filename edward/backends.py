"""Scorer backends: pluggable judgment engines behind one contract.

A backend answers Edward's judgment questions — (state, question, options) →
{choice, confidence, probabilities, ...} — regardless of where the judgment
comes from. Three backends ship built in:

- EndpointBackend   the LAN /v1/score server (default; the historical protocol)
- JevBackend        TypeSafe Jev via POST /v1/systemone — batches every
                    question into ONE call (fan-out pattern), noul/choice
                    calibrated answers
- HeuristicBackend  deterministic marker-based stub; offline demos and tests.
                    NOT a real judge — confidence is fixed and low.

All backends are advisory: every None / failure path degrades to the
deterministic rule action (engine handles that centrally via Scorer's
circuit breaker). Zero dependencies: transport is urllib, and tests inject
a fake post_fn — no network in the test suite.
"""

import json
import os
import urllib.request
from typing import Callable, Optional

from .scorer_client import ScorerClient


JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

PostFn = Callable[[str, dict, dict, float], dict]


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class JudgmentBackend:
    """Interface: ask one question, or batch several into one round trip."""

    name = "base"

    def __init__(self, timeout: float = 10.0, post_fn: Optional[PostFn] = None):
        self.timeout = timeout
        self._post = post_fn or _post_json

    def health(self) -> Optional[dict]:
        raise NotImplementedError

    def ask(self, state, question: str, options: dict) -> Optional[dict]:
        results = self.ask_many(state, {"q": (question, options)})
        return results.get("q")

    def score(self, state, question: str, options: dict) -> Optional[dict]:
        """Compatibility alias for call sites written against ScorerClient."""
        return self.ask(state, question, options)

    def ask_many(self, state, questions: dict) -> dict:
        """questions: {qid: (question, options)} → {qid: judgment-dict}.

        Default: sequential single calls. Backends with a batched wire
        protocol (Jev) override this.
        """
        out = {}
        for qid, (question, options) in questions.items():
            out[qid] = self._ask_one(state, question, options)
        return out

    def _ask_one(self, state, question: str, options: dict) -> Optional[dict]:
        raise NotImplementedError


class EndpointBackend(JudgmentBackend):
    """The historical LAN scorer: POST {base_url}/v1/score."""

    name = "endpoint"

    def __init__(self, base_url: str, timeout: float = 10.0, post_fn=None):
        super().__init__(timeout=timeout, post_fn=post_fn)
        self.client = ScorerClient(base_url=base_url, timeout=timeout,
                                   post_fn=post_fn)

    def health(self) -> Optional[dict]:
        return self.client.health()

    def _ask_one(self, state, question: str, options: dict) -> Optional[dict]:
        return self.client.score(state, question, options)

    def ask_many(self, state, questions: dict) -> dict:
        return {qid: self._ask_one(state, q, o) for qid, (q, o) in questions.items()}


class JevBackend(JudgmentBackend):
    """TypeSafe Jev: every question in one batched /v1/systemone call.

    Maps Edward's options-based judgment contract onto Jev Choice questions:
    each option id + description becomes a criterion. Answers come back with
    calibrated probabilities and confidence; call-level token usage is
    attached to every judgment (documented as call-level, not per-question).
    """

    name = "jev"

    def __init__(self, api_key: str, api_url: str = JEV_API_URL,
                 model: str = JEV_MODEL, timeout: float = 10.0, post_fn=None):
        super().__init__(timeout=timeout, post_fn=post_fn)
        self.api_key = api_key
        self.api_url = api_url
        self.model = model
        self.last_usage: Optional[dict] = None

    def health(self) -> Optional[dict]:
        try:
            self._post(
                self.api_url,
                {"state": "ping", "model": self.model,
                 "questions": {"health": {"type": "noul",
                                          "instructions": "Reply yes."}}},
                self._headers(), self.timeout,
            )
            return {"ready": True, "model": self.model, "backend": self.name}
        except Exception:
            return None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def ask_many(self, state, questions: dict) -> dict:
        self.last_usage = None
        payload = {
            "state": self._prepare_state(state),
            "model": self.model,
            "questions": {
                qid: {"type": "choice",
                      "instructions": question,
                      "criteria": dict(options)}
                for qid, (question, options) in questions.items()
            },
        }
        try:
            data = self._post(self.api_url, payload, self._headers(), self.timeout)
        except Exception:
            return {qid: None for qid in questions}
        answers = data.get("answers") or {}
        self.last_usage = data.get("usage")
        out = {}
        for qid in questions:
            ans = answers.get(qid)
            if not isinstance(ans, dict) or ans.get("type") != "choice":
                out[qid] = None
                continue
            out[qid] = {
                "choice": ans.get("choice"),
                "confidence": ans.get("confidence"),
                "probabilities": ans.get("probabilities") or {},
                "backend": self.name,
                "input_tokens": (self.last_usage or {}).get("input_tokens"),
                "total_seconds": None,
            }
        return out

    def _prepare_state(self, state) -> object:
        # Jev accepts string | object | array of text; compact-dump anything
        # exotic and cap size the same way the endpoint backend does.
        from .scorer_client import MAX_STATE_CHARS
        encoded = json.dumps(state, separators=(",", ":"), default=str)
        if len(encoded) <= MAX_STATE_CHARS:
            return state
        return encoded[:MAX_STATE_CHARS]


class HeuristicBackend(JudgmentBackend):
    """Deterministic marker-based stub for offline demos and tests.

    Reads the state as lowercase text and applies fixed markers. Confidence
    is always 0.6 so downstream confidence-gating treats it as weak. It is a
    plumbing exercise, not a judgment engine — the README says so.
    """

    name = "heuristic"

    DANGER = ("rm -rf", "drop table", "force-push", "git push --force",
              ":(){", "mkfs", "dd if=")
    LOOP = ("repeated", "same error", "no progress", "still failing",
            "retrying", "same test")

    def health(self) -> Optional[dict]:
        return {"ready": True, "model": "heuristic-markers", "backend": self.name}

    def _ask_one(self, state, question: str, options: dict) -> Optional[dict]:
        if not options:
            return None
        text = json.dumps(state, separators=(",", ":"), default=str).lower()
        danger = any(m in text for m in self.DANGER)
        loop = any(m in text for m in self.LOOP)
        preferred = "BLOCK" if ("BLOCK" in options and danger) else (
            "REQUEST_HUMAN_APPROVAL" if danger and "REQUEST_HUMAN_APPROVAL" in options else (
                "PAUSE" if ("PAUSE" in options and loop) else next(iter(options))))
        even = 1.0 / len(options)
        probabilities = {oid: round(even, 4) for oid in options}
        probabilities[preferred] = round(1.0 - even * (len(options) - 1), 4)
        return {"choice": preferred, "confidence": 0.6,
                "probabilities": probabilities, "backend": self.name,
                "input_tokens": None, "total_seconds": None}

    def ask_many(self, state, questions: dict) -> dict:
        return {qid: self._ask_one(state, q, o) for qid, (q, o) in questions.items()}


class DisabledBackend(JudgmentBackend):
    """Always-None backend: scorer explicitly disabled or misconfigured."""

    name = "disabled"

    def health(self) -> Optional[dict]:
        return None

    def ask_many(self, state, questions: dict) -> dict:
        return {qid: None for qid in questions}


def backend_from_env(scorer_base_url: str, timeout: float,
                     post_fn: Optional[PostFn] = None
                     ) -> tuple[JudgmentBackend, str]:
    """Resolve EDWARD_SCORER_BACKEND (endpoint|jev|heuristic|off).

    Returns (backend, note). Never raises: unknown values and missing Jev
    keys degrade to disabled (rule-only) with an explanatory note.
    """
    kind = os.environ.get("EDWARD_SCORER_BACKEND", "endpoint").strip().lower()
    if kind in ("endpoint", "openai", ""):
        return EndpointBackend(scorer_base_url, timeout, post_fn), "endpoint"
    if kind == "heuristic":
        return HeuristicBackend(timeout=timeout, post_fn=post_fn), "heuristic"
    if kind == "off":
        return DisabledBackend(timeout=timeout), "scorer disabled by config"
    if kind == "jev":
        key = os.environ.get("TYPESAFE_API_KEY", "")
        if not key:
            return (DisabledBackend(timeout=timeout),
                    "EDWARD_SCORER_BACKEND=jev but TYPESAFE_API_KEY not set — "
                    "running rule-only")
        url = os.environ.get("JEV_API_URL", JEV_API_URL)
        model = os.environ.get("JEV_MODEL", JEV_MODEL)
        return JevBackend(api_key=key, api_url=url, model=model,
                          timeout=timeout, post_fn=post_fn), "jev"
    return (DisabledBackend(timeout=timeout),
            f"unknown EDWARD_SCORER_BACKEND={kind!r} — running rule-only")
