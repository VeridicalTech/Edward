"""Backend abstraction tests — fully offline via injected post_fn fixtures."""

import os
import unittest
from unittest import mock

from edward.backends import (DisabledBackend, EndpointBackend, HeuristicBackend,
                             JevBackend, backend_from_env)
from edward.scorer import Scorer, make_scorer
from edward.config import Policy

# Recorded live response (2026-09-23, jev-1.13.0) — trimmed to 2 questions.
JEV_BATCH_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "loop": {"type": "noul", "noul": 0.86},
        "quality": {
            "type": "choice", "choice": "critical", "confidence": 0.27,
            "probabilities": {"healthy": 0.0, "worrying": 0.49, "critical": 0.51},
        },
    },
    "usage": {"input_tokens": 746, "output_tokens": 96},
}

ENDPOINT_RESPONSE = {
    "option_ids": ["CONTINUE", "PAUSE", "CANCEL", "ESCALATE"],
    "probabilities": [0.1, 0.7, 0.15, 0.05],
    "prompt_version": "v1b",
    "input_tokens": 312,
}

STATE = {"turns": 14, "last_output": "FAILED tests/test_api.py::test_retry"}
QUESTIONS = {
    "loop": ("Is the agent stuck in a loop?",
             {"continue": "progressing", "pause": "struggling"}),
    "quality": ("Overall trajectory health?",
                {"healthy": "steady", "worrying": "waste", "critical": "harmful"}),
}


class TestJevBackend(unittest.TestCase):
    def _backend(self, post_fn):
        return JevBackend(api_key="test-key", post_fn=post_fn)

    def test_batches_all_questions_into_one_call(self):
        calls = []

        def post_fn(url, payload, headers, timeout):
            calls.append((url, payload, headers))
            return JEV_BATCH_RESPONSE

        b = self._backend(post_fn)
        out = b.ask_many(STATE, QUESTIONS)
        self.assertEqual(len(calls), 1, "N questions must be ONE wire call")
        url, payload, headers = calls[0]
        self.assertEqual(url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(payload["model"], "jev-latest")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(set(payload["questions"]), {"loop", "quality"})
        q = payload["questions"]["quality"]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(q["criteria"],
                         {"healthy": "steady", "worrying": "waste",
                          "critical": "harmful"})

    def test_maps_choice_answers_and_usage(self):
        b = self._backend(lambda *a: JEV_BATCH_RESPONSE)
        out = b.ask_many(STATE, QUESTIONS)
        q = out["quality"]
        self.assertEqual(q["choice"], "critical")
        self.assertEqual(q["confidence"], 0.27)
        self.assertEqual(q["probabilities"]["critical"], 0.51)
        self.assertEqual(q["input_tokens"], 746, "call-level usage attached")
        self.assertEqual(q["backend"], "jev")

    def test_non_choice_answer_maps_to_none(self):
        # noul answer under a choice-contract question id → unusable → None
        b = self._backend(lambda *a: JEV_BATCH_RESPONSE)
        out = b.ask_many(STATE, QUESTIONS)
        self.assertIsNone(out["loop"])

    def test_transport_failure_degrades_to_none(self):
        def boom(*a):
            raise OSError("network down")
        b = self._backend(boom)
        out = b.ask_many(STATE, QUESTIONS)
        self.assertEqual(out, {"loop": None, "quality": None})

    def test_oversized_state_is_compacted(self):
        seen = {}

        def post_fn(url, payload, headers, timeout):
            seen["state"] = payload["state"]
            return JEV_BATCH_RESPONSE

        b = self._backend(post_fn)
        b.ask_many({"blob": "x" * 20000}, QUESTIONS)
        self.assertIsInstance(seen["state"], str)
        self.assertLessEqual(len(seen["state"]), 12000)


class TestEndpointBackend(unittest.TestCase):
    def test_maps_recorded_response(self):
        def post_fn(url, payload, headers, timeout):
            self.assertTrue(url.endswith("/v1/score"))
            self.assertEqual(payload["options"][0]["id"], "CONTINUE")
            return ENDPOINT_RESPONSE

        b = EndpointBackend("http://localhost:8000", post_fn=post_fn)
        out = b.ask(STATE, "should the agent continue?", {
            "CONTINUE": "keep going", "PAUSE": "struggling",
            "CANCEL": "stuck", "ESCALATE": "human now"})
        self.assertEqual(out["choice"], "PAUSE")
        self.assertAlmostEqual(out["confidence"], 0.7)
        self.assertEqual(out["prompt_version"], "v1b")


class TestHeuristicBackend(unittest.TestCase):
    def test_loop_markers_pause(self):
        b = HeuristicBackend()
        state = {"log": "same error again, still failing, retrying"}
        out = b.ask(state, "continue?", dict.fromkeys(["CONTINUE", "PAUSE"]))
        self.assertEqual(out["choice"], "PAUSE")
        self.assertEqual(out["confidence"], 0.6)
        self.assertAlmostEqual(sum(out["probabilities"].values()), 1.0)

    def test_danger_markers_block(self):
        b = HeuristicBackend()
        out = b.ask({"cmd": "rm -rf /var/data"}, "safe?",
                    dict.fromkeys(["ALLOW", "REQUEST_HUMAN_APPROVAL", "BLOCK"]))
        self.assertEqual(out["choice"], "BLOCK")

    def test_clean_state_continues(self):
        b = HeuristicBackend()
        out = b.ask({"cmd": "pytest -k slow"}, "continue?",
                    dict.fromkeys(["CONTINUE", "PAUSE"]))
        self.assertEqual(out["choice"], "CONTINUE")


class TestBackendFactory(unittest.TestCase):
    def test_default_endpoint(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDWARD_SCORER_BACKEND", None)
            b, note = backend_from_env("http://localhost:8000", 10.0)
        self.assertEqual(b.name, "endpoint")

    def test_jev_without_key_degrades_disabled(self):
        with mock.patch.dict(os.environ, {"EDWARD_SCORER_BACKEND": "jev"}):
            os.environ.pop("TYPESAFE_API_KEY", None)
            b, note = backend_from_env("http://x", 10.0)
        self.assertIsInstance(b, DisabledBackend)
        self.assertIn("TYPESAFE_API_KEY", note)

    def test_jev_with_key(self):
        env = {"EDWARD_SCORER_BACKEND": "jev", "TYPESAFE_API_KEY": "k"}
        with mock.patch.dict(os.environ, env):
            b, _ = backend_from_env("http://x", 10.0)
        self.assertEqual(b.name, "jev")

    def test_heuristic_and_off_and_unknown(self):
        for val, expect in [("heuristic", "heuristic"), ("off", "disabled"),
                            ("wat", "disabled")]:
            with mock.patch.dict(os.environ, {"EDWARD_SCORER_BACKEND": val}):
                b, _ = backend_from_env("http://x", 10.0)
            self.assertEqual(b.name, expect, val)


class TestScorerWithBackends(unittest.TestCase):
    def _policy(self):
        with mock.patch.dict(os.environ, {"EDWARD_SCORER_BACKEND": "endpoint"}):
            os.environ.pop("EDWARD_SCORER_URL", None)
            return Policy()

    def test_make_scorer_endpoint_parity(self):
        scorer = make_scorer(self._policy(),
                             post_fn=lambda *a: ENDPOINT_RESPONSE)
        self.assertEqual(scorer.name, "endpoint")
        out = scorer.consult(STATE)
        self.assertEqual(out["choice"], "PAUSE")

    def test_breaker_opens_after_two_failures(self):
        calls = []

        def boom(*a):
            calls.append(1)
            raise OSError("down")

        with mock.patch.dict(os.environ, {"EDWARD_SCORER_BACKEND": "endpoint"}):
            scorer = make_scorer(self._policy(), post_fn=boom)
        self.assertIsNone(scorer.consult(STATE))
        self.assertIsNone(scorer.consult(STATE))
        self.assertGreaterEqual(scorer.open_until, 1)
        third = scorer.consult(STATE)  # breaker open: no wire call
        self.assertIsNone(third)
        self.assertEqual(len(calls), 2)

    def test_disabled_backend_is_rule_only(self):
        with mock.patch.dict(os.environ, {"EDWARD_SCORER_BACKEND": "off"}):
            scorer = make_scorer(self._policy())
        self.assertEqual(scorer.name, "disabled")
        self.assertIsNone(scorer.consult(STATE))


if __name__ == "__main__":
    unittest.main()
