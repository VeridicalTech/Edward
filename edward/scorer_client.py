import json
import urllib.request
from typing import Optional


SCORER_API_URL = "http://localhost:8000"

CONTINUE_OPTIONS = {
    "CONTINUE": "The agent is making reasonable progress and should keep going",
    "PAUSE": "The agent shows signs of struggle; pause for review",
    "CANCEL": "The agent is stuck or wasting resources; terminate",
    "ESCALATE": "Immediate human intervention required",
}

PERMISSION_OPTIONS = {
    "ALLOW": "The command is safe and routine",
    "REQUEST_HUMAN_APPROVAL": "The command could cause damage; needs human confirmation",
    "BLOCK": "The command is clearly dangerous; block immediately",
}

MAX_STATE_CHARS = 12_000


class ScorerClient:
    def __init__(self, base_url: str = SCORER_API_URL, timeout: float = 10.0,
                 post_fn=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._post_fn = post_fn
        self._request_count = 0

    def health(self) -> Optional[dict]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    def ask_continue(self, mss: dict, trigger_reason: str = "") -> Optional[dict]:
        question = "Based on the agent state, should the agent continue executing its current task?"
        if trigger_reason:
            question = f"The external control plane fired a deterministic trigger: {trigger_reason}. Given this and the agent state, should the agent continue?"
        return self._score(mss, question, CONTINUE_OPTIONS)

    def ask_permission(self, mss: dict) -> Optional[dict]:
        return self._score(
            mss,
            "Is the agent's recent command safe to execute without human approval?",
            PERMISSION_OPTIONS,
        )

    def score(self, state, question: str, options: dict) -> Optional[dict]:
        """Generic single-decision scoring (public contract-probe entry)."""
        return self._score(state, question, options)

    def _score(self, state, question: str, options: dict) -> Optional[dict]:
        self._request_count += 1
        payload = {
            "id": f"jev-{self._request_count}",
            "state": self._prepare_state(state),
            "question": question,
            "options": [{"id": oid, "description": desc} for oid, desc in options.items()],
        }
        try:
            headers = {"Content-Type": "application/json"}
            if self._post_fn is not None:
                data = self._post_fn(f"{self.base_url}/v1/score", payload,
                                     headers, self.timeout)
            else:
                req = urllib.request.Request(
                    f"{self.base_url}/v1/score",
                    data=json.dumps(payload).encode(),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
        except Exception:
            return None
        return self._decide(data)

    @staticmethod
    def _prepare_state(state) -> object:
        encoded = json.dumps(state, separators=(",", ":"), default=str)
        if len(encoded) <= MAX_STATE_CHARS:
            return state
        return encoded[:MAX_STATE_CHARS]

    @staticmethod
    def _decide(data: dict) -> Optional[dict]:
        option_ids = data.get("option_ids") or []
        probabilities = data.get("probabilities") or []
        if not option_ids or len(option_ids) != len(probabilities):
            return None
        best = max(range(len(probabilities)), key=lambda i: probabilities[i])
        return {
            "choice": option_ids[best],
            "confidence": probabilities[best],
            "probabilities": dict(zip(option_ids, probabilities)),
            "prompt_version": data.get("prompt_version"),
            "total_seconds": data.get("total_seconds"),
            "input_tokens": data.get("input_tokens"),
        }
