"""--style vllm direct readout: canned vLLM responses (documented shapes:
/tokenize -> {"tokens":[id]}; /v1/completions logprobs.top_logprobs =
[{token: logprob}]), the missing-letter follow-up path, and request
well-formedness (allowed_token_ids, max_tokens=1, raw ChatML prompt)."""
import unittest

from edward.scorer_server import ScorerServer


class FakeVLLM:
    def __init__(self, top_row):
        self.top_row = top_row          # first completion's top_logprobs row
        self.tokenize_calls = 0
        self.completions = []

    def __call__(self, url, payload, headers, timeout):
        if url.endswith("/tokenize"):
            self.tokenize_calls += 1
            letter = payload["prompt"]
            return {"tokens": [ord(letter)]}   # A->65, B->66, C->67
        if url.endswith("/completions"):
            self.completions.append(payload)
            allow = payload.get("allowed_token_ids") or []
            if len(allow) == 1:                # constrained follow-up
                letter = chr(allow[0])
                return {"choices": [{"text": letter,
                                     "logprobs": {"top_logprobs": [{letter: -4.0}]}}]}
            return {"choices": [{"text": "A",
                                 "logprobs": {"top_logprobs": [self.top_row]}}]}
        raise AssertionError(f"unexpected url {url}")


class TestVllmDirectReadout(unittest.TestCase):
    def test_softmax_and_followup(self):
        fake = FakeVLLM({"A": -0.2, "B": -2.3})     # C missing from top-k
        server = ScorerServer("http://x:8000/v1", "m", style="vllm", post_fn=fake)
        out = server.score({"task_title": "t"}, "classify this",
                           {"OK": "benign", "VIOLATION": "violates",
                            "UNSURE": "ambiguous"})
        # softmax(-0.2, -2.3, -4.0): A .8735 B .1070 C .0195
        self.assertAlmostEqual(out["probabilities"][0], 0.8735, places=3)
        self.assertAlmostEqual(out["probabilities"][1], 0.1070, places=3)
        self.assertAlmostEqual(out["probabilities"][2], 0.0195, places=3)
        self.assertEqual(out["choice"], "OK")
        self.assertEqual(out["prompt_version"], "semif-direct-v1")
        self.assertEqual(fake.tokenize_calls, 3)     # A, B, C

    def test_request_shape(self):
        fake = FakeVLLM({"A": -0.01})
        server = ScorerServer("http://x:8000/v1", "m", style="vllm", post_fn=fake)
        server.score({}, "q", {"OK": "a", "VIOLATION": "b", "UNSURE": "c"})
        body = fake.completions[0]
        self.assertEqual(body["max_tokens"], 1)
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["allowed_token_ids"], [65, 66, 67])
        self.assertIn("<think>", body["prompt"])     # raw ChatML, empty think

    def test_followup_rescues_all_letters_uniform(self):
        # top-k has no letters, but the constrained follow-up fetches each
        # letter's raw logprob — that is the vllm style's whole point. Equal
        # logprobs -> uniform distribution, first option wins the tie.
        fake = FakeVLLM({"x": -0.5, "y": -0.6})
        server = ScorerServer("http://x:8000/v1", "m", style="vllm", post_fn=fake)
        out = server.score({}, "q", {"OK": "a", "VIOLATION": "b", "UNSURE": "c"})
        self.assertEqual(out["choice"], "OK")
        for p in out["probabilities"]:
            self.assertAlmostEqual(p, 1 / 3, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
