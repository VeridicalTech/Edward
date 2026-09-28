"""v0.4 features: done_checks (verification-gated convergence), Pareto
intervention selection, scorer-coverage audit."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from edward.audit import AuditLog, summarize
from edward.backends import DisabledBackend, JevBackend
from edward.config import Policy, load_policy
from edward.engine import ControlPlane
from edward.scorer import Scorer


def stall_events(n_reads=14):
    events = [{"type": "agent_start"}]
    for i in range(n_reads):
        events.append({"type": "turn_start"})
        events.append({"type": "tool_execution_start", "toolCallId": f"c{i}",
                       "toolName": "read", "args": {"path": "f.txt"}})
        events.append({"type": "tool_execution_end", "toolCallId": f"c{i}",
                       "toolName": "read", "args": {"path": "f.txt"}, "isError": False})
    return events


def quiet_policy(**overrides):
    p = load_policy("balanced")
    p.stderr_banner = False
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


class ScriptedParetoBackend(DisabledBackend):
    name = "scripted"

    def __init__(self, recover, risk):
        super().__init__()
        self.recover, self.risk = recover, risk

    def ask_many(self, state, questions):
        out = {}
        for qid in questions:
            if qid == "risk_inaction":
                p = self.risk
            else:
                p = self.recover[qid.replace("recover_", "")]
            out[qid] = {"choice": "yes", "confidence": p,
                        "probabilities": {"yes": p, "no": round(1 - p, 4)},
                        "backend": self.name}
        return out


class LegacyBackend(DisabledBackend):
    name = "legacy"

    def ask(self, state, question, options):
        return {"choice": "PAUSE", "confidence": 0.9,
                "probabilities": {"PAUSE": 0.9}, "backend": self.name}


class TestDoneChecks(unittest.TestCase):
    def _feed_convergence(self, td, checker):
        t = [0.0]

        def clock():
            t[0] += 100.0
            return t[0]

        path = os.path.join(td, "a.jsonl")
        plane = ControlPlane(quiet_policy(), session="s", audit=AuditLog(path),
                             clock=clock, done_checker=checker)
        decision = None
        for ev in stall_events(8):
            decision = plane.process_event(ev)
            if decision:
                break
        return decision, path

    def test_passing_check_suppresses_premature_pause(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []
            def checker():
                calls.append(1)
                return True, "1 passed"
            decision, path = self._feed_convergence(td, checker)
            self.assertIsNone(decision, "convergence PAUSE must be suppressed")
            self.assertGreaterEqual(len(calls), 1, "checker consulted at trigger time")
            d = summarize(path)
            self.assertGreaterEqual(d["done_verified"], 1)

    def test_failing_check_becomes_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            decision, path = self._feed_convergence(
                td, lambda: (False, "pytest -q (rc=1) 3 failed"))
            self.assertIsNotNone(decision)
            self.assertEqual(decision.action, "PAUSE")
            self.assertIn("done check FAILED", decision.reason)
            self.assertIn("3 failed", decision.reason)

    def test_checker_error_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            def boom():
                raise RuntimeError("no tests configured")
            decision, _ = self._feed_convergence(td, boom)
            self.assertIsNotNone(decision)
            self.assertIn("checker error", decision.reason)


class TestParetoIntervention(unittest.TestCase):
    def _feed_stall(self, td, policy, scorer):
        path = os.path.join(td, "a.jsonl")
        plane = ControlPlane(policy, session="s", audit=AuditLog(path), scorer=scorer)
        decision = None
        for ev in stall_events():
            decision = plane.process_event(ev)
            if decision:
                break
        return decision

    def test_high_risk_least_severe_recovering_action_wins(self):
        with tempfile.TemporaryDirectory() as td:
            scorer = Scorer(backend=ScriptedParetoBackend(
                recover={"continue": 0.1, "pause": 0.9, "cancel": 0.2}, risk=0.9))
            policy = quiet_policy(pareto_intervention=True)
            d = self._feed_stall(td, policy, scorer)
            self.assertEqual(d.action, "PAUSE")
            self.assertIn("least-severe", d.source)
            self.assertEqual(d.jev["risk_inaction"], 0.9)

    def test_low_risk_recovery_likely_continues(self):
        with tempfile.TemporaryDirectory() as td:
            scorer = Scorer(backend=ScriptedParetoBackend(
                recover={"continue": 0.9, "pause": 0.8, "cancel": 0.1}, risk=0.3))
            policy = quiet_policy(pareto_intervention=True)
            d = self._feed_stall(td, policy, scorer)
            self.assertEqual(d.action, "CONTINUE")

    def test_no_candidate_recovers_safety_first_cancel(self):
        with tempfile.TemporaryDirectory() as td:
            scorer = Scorer(backend=ScriptedParetoBackend(
                recover={"continue": 0.2, "pause": 0.4, "cancel": 0.3}, risk=0.9))
            policy = quiet_policy(pareto_intervention=True)
            d = self._feed_stall(td, policy, scorer)
            self.assertEqual(d.action, "CANCEL")
            self.assertIn("safety first", d.source)

    def test_flag_off_uses_legacy_single_question(self):
        with tempfile.TemporaryDirectory() as td:
            scorer = Scorer(backend=LegacyBackend())
            policy = quiet_policy(pareto_intervention=False)
            d = self._feed_stall(td, policy, scorer)
            self.assertEqual(d.action, "PAUSE")
            self.assertIn("jev (conf 0.90)", d.source)

    def test_pareto_unresolved_degrades_to_rule(self):
        with tempfile.TemporaryDirectory() as td:
            scorer = Scorer(backend=DisabledBackend())
            policy = quiet_policy(pareto_intervention=True)
            d = self._feed_stall(td, policy, scorer)
            self.assertEqual(d.action, "PAUSE")
            self.assertIn("rule", d.source)


class TestCoverageAudit(unittest.TestCase):
    def test_coverage_counts_what_the_guard_saw(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "a.jsonl")
            audit = AuditLog(path)
            audit.emit("scorer_stats", session="s", tool_calls_seen=14,
                       scorer_consultations=2, scorer_failures=1)
            d = summarize(path)
            self.assertEqual(d["coverage"]["tool_calls_seen"], 14)
            self.assertEqual(d["coverage"]["scorer_consultations"], 2)
            self.assertEqual(d["coverage"]["scorer_failures"], 1)

    def test_plane_coverage_method(self):
        plane = ControlPlane(quiet_policy(), session="s",
                             scorer=Scorer(backend=DisabledBackend()))
        for ev in stall_events(3):
            plane.process_event(ev)
        cov = plane.coverage()
        self.assertEqual(cov["tool_calls_seen"], 3)
        self.assertEqual(cov["scorer_consultations"], 0)


class TestPolicyFields(unittest.TestCase):
    def test_done_checks_and_pareto_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, "p.toml")
            Path(pf).write_text(
                'done_checks = ["pytest -q", "ruff check ."]\n'
                'pareto_intervention = true\n')
            policy = load_policy(pf)
            self.assertEqual(policy.done_checks, ["pytest -q", "ruff check ."])
            self.assertTrue(policy.pareto_intervention)
            from edward.config import policy_toml
            rt = load_policy.__globals__  # noqa — just ensure import path works
            toml_text = __import__("edward.config", fromlist=["policy_toml"]).policy_toml(policy)
            self.assertIn('done_checks = ["pytest -q", "ruff check ."]', toml_text)
            self.assertIn("pareto_intervention = true", toml_text)

    def test_done_checks_type_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, "p.toml")
            Path(pf).write_text("done_checks = [1, 2]\n")
            from edward.config import PolicyError
            with self.assertRaises(PolicyError):
                load_policy(pf)


if __name__ == "__main__":
    unittest.main()
