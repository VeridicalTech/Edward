"""edward replay: fixture audit records -> self-contained HTML receipt view."""
import json
import tempfile
import unittest
from pathlib import Path

from edward.replay import render, sessions_in, write_replay


def _audit(recs):
    td = tempfile.TemporaryDirectory()
    p = Path(td.name) / "audit.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    return td, p


RECS = [
    {"ts": "2026-10-08T09:00:00+0800", "v": 1, "type": "session_start",
     "session": "aaa11111", "policy_preset": "balanced",
     "command": ["pi", "fix the flaky test"]},
    {"ts": "2026-10-08T09:01:00+0800", "v": 1, "type": "intervention",
     "session": "aaa11111", "trigger_reason": "Output inactivity: 90s",
     "decision_type": "deterministic", "action": "PAUSE",
     "source": "rule (inactivity watchdog)", "authority": "policy",
     "mss": {"token_usage": 12000}, "jev": {"choice": "PAUSE", "confidence": 0.9},
     "est_avoided_usd": 0.6},
    {"ts": "2026-10-08T09:02:00+0800", "v": 1, "type": "session_end",
     "session": "aaa11111", "reason": "exit 75", "exit_code": 75},
]


class TestReplay(unittest.TestCase):
    def test_render_markers(self):
        td, p = _audit(RECS)
        try:
            html_text, sid = render(p)
            self.assertEqual(sid, "aaa11111")
            self.assertIn("SESSION START", html_text)
            self.assertIn("badge PAUSE", html_text)
            self.assertIn("fix the flaky test", html_text)
            self.assertIn("Output inactivity: 90s", html_text)
            self.assertIn("$0.60", html_text)
            self.assertIn("SESSION END", html_text)
            self.assertIn("edward verify", html_text)
        finally:
            td.cleanup()

    def test_last_session_default_and_missing_error(self):
        recs = RECS + [{"ts": "2026-10-08T10:00:00+0800", "v": 1,
                        "type": "session_start", "session": "bbb22222",
                        "policy_preset": "ci", "command": ["x"]}]

        td, p = _audit(recs)
        try:
            _, sid = render(p)                 # default = most recent
            self.assertEqual(sid, "bbb22222")
            with self.assertRaises(ValueError):
                render(p, session="nope")
        finally:
            td.cleanup()

    def test_write_replay_output_path(self):
        td, p = _audit(RECS)
        try:
            out, sid = write_replay(p)
            self.assertTrue(out.exists())
            self.assertIn("aaa11111", out.name)
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
