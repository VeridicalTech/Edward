"""Product test suite for the edward package (offline, stdlib unittest).

Run: python3 -m unittest test_product -v
Covers: policy loading/validation, FROZEN regression, audit, circuit
breaker, control-plane decisions (incl. disagreement-conservative),
cooldown, CLI helpers, demo/eval gates.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from edward.audit import AuditLog, summarize
from edward.config import PolicyError, TRIGGER_DEFAULTS, load_policy, policy_toml
from edward.engine import ControlPlane
from edward.cli import (_extract_pi_prompt, _last_paused_session, _split_cmd,
                            build_pi_command, EXIT_PAUSED, EXIT_TERMINATED)
from edward.approval import ApprovalServer
from edward.scorer import Scorer
from edward.triggers import check_triggers
from edward.state_engine import StateEngine
from edward.scenarios import SCENARIOS, run_trial
from edward.evalcmd import eval_policy, verdict


def quiet_policy(**overrides):
    from edward.config import load_policy as lp
    p = lp("balanced")
    p.stderr_banner = False
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


class FakeScorer:
    def __init__(self, choice="CONTINUE", confidence=0.99):
        self.choice = choice
        self.confidence = confidence
        self.calls = 0

    def consult(self, mss, reason=""):
        self.calls += 1
        return {"choice": self.choice, "confidence": self.confidence,
                "probabilities": {self.choice: self.confidence}}


class BrokenScorer:
    def consult(self, mss, reason=""):
        raise RuntimeError("network down")


def feed(plane, events):
    decision = None
    for ev in events:
        decision = plane.process_event(ev)
        if decision:
            break
    return decision


def read_events(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


class TestPolicy(unittest.TestCase):
    def test_presets_load(self):
        for name in ("conservative", "balanced", "aggressive"):
            p = load_policy(name)
            self.assertEqual(p.preset, name)
        self.assertEqual(load_policy().triggers, TRIGGER_DEFAULTS)

    def test_toml_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.toml")
            Path(path).write_text(policy_toml(load_policy("conservative")), encoding="utf-8")
            p = load_policy(path)
            self.assertEqual(p.preset, "conservative")
            self.assertEqual(p.triggers["passive_read_streak"], 10)

    def test_toml_partial_override(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.toml")
            Path(path).write_text(
                'preset = "balanced"\n[triggers]\nretry_count = 7\n[scorer]\nbase_url = "http://x:1"\n',
                encoding="utf-8")
            p = load_policy(path)
            self.assertEqual(p.triggers["retry_count"], 7)
            self.assertEqual(p.triggers["error_rate"], TRIGGER_DEFAULTS["error_rate"])
            self.assertEqual(p.scorer_base_url, "http://x:1")

    def test_json_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.json")
            Path(path).write_text(json.dumps({"allowed_paths": ["/tmp/x"], "token_budget": 1000}), encoding="utf-8")
            p = load_policy(path)
            self.assertEqual(p.allowed_paths, ["/tmp/x"])
            self.assertEqual(p.token_budget, 1000)

    def test_unknown_key_warns_not_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.toml")
            Path(path).write_text('frobnicate = 1\n', encoding="utf-8")
            p = load_policy(path)
            self.assertEqual(p.token_budget, 200_000)

    def test_bad_types_raise(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.toml")
            Path(path).write_text('token_budget = "lots"', encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)
            Path(path).write_text('allowed_paths = "src"', encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_missing_file_raises(self):
        with self.assertRaises(PolicyError):
            load_policy("/nonexistent/policy.toml")


class TestFrozenRegression(unittest.TestCase):
    def test_default_thresholds_unchanged(self):
        self.assertEqual(TRIGGER_DEFAULTS, {
            "error_rate": 0.4, "error_rate_window": 8, "retry_count": 3,
            "convergence_seconds": 600, "convergence_turns": 5,
            "passive_read_streak": 12, "unverified_write_streak": 10,
            "budget_pct": 0.8,
        })

    def test_balanced_suite_gate(self):
        p = quiet_policy()
        metrics = eval_policy(p, n_trials=5, seed=7)
        self.assertTrue(verdict(metrics), f"balanced gate failed: {metrics}")


class TestAudit(unittest.TestCase):
    def test_write_and_summarize(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audit.jsonl")
            log = AuditLog(path)
            log.session_start("s1", "balanced", ["pi", "task"])
            log.intervention("s1", "loop", "should_continue", "PAUSE", "rule",
                             "soft_decision", {"token_usage": 900, "cost_usd": 0.02},
                             est_avoided_usd=0.60)
            log.session_end("s1", "exit 75", 75)
            recs = read_events(path)
            self.assertEqual(len(recs), 3)
            self.assertEqual(recs[0]["type"], "session_start")
            s = summarize(path)
            self.assertEqual(s["interventions"], 1)
            self.assertEqual(s["sessions"], 1)
            self.assertEqual(s["by_action"], {"PAUSE": 1})
            self.assertAlmostEqual(s["est_avoided_usd"], 0.60)

    def test_degrades_on_bad_path(self):
        with tempfile.TemporaryDirectory() as td:
            blocker = os.path.join(td, "blocker")  # a FILE used as a parent dir
            open(blocker, "w").close()
            log = AuditLog(os.path.join(blocker, "audit.jsonl"))
            log.emit("intervention", session="s", action="PAUSE")  # must not raise
            self.assertTrue(log._degraded)


class FakeBreakerClient:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def ask_continue(self, mss, trigger_reason=""):
        self.calls += 1
        if self.calls <= self.fail_times:
            return None
        return {"choice": "PAUSE", "confidence": 0.9,
                "probabilities": {"PAUSE": 0.9}}

    def health(self):
        return {"status": "ok", "ready": True, "model": "fake"}


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_two_failures(self):
        s = Scorer("http://fake:1")
        s.client = FakeBreakerClient(fail_times=2)
        self.assertIsNone(s.consult({}, "r"))
        self.assertIsNone(s.consult({}, "r"))
        calls_after_open = s.client.calls
        self.assertIsNone(s.consult({}, "r"))  # breaker open: no call
        self.assertEqual(s.client.calls, calls_after_open)

    def test_recovers_on_success(self):
        s = Scorer("http://fake:1")
        s.client = FakeBreakerClient(fail_times=2)
        s.consult({}, "r")
        s.consult({}, "r")
        r = s.consult({}, "r")  # breaker expired? cooldown 60s — force:
        s.open_until = 0.0
        r = s.consult({}, "r")
        self.assertIsNotNone(r)
        self.assertEqual(s.fail_streak, 0)


def stall_events(n_reads=14):
    events = [{"type": "agent_start"}]
    for _ in range(n_reads):
        events.append({"type": "turn_start"})
        events.append({"type": "tool_execution_end", "toolName": "read",
                       "args": {"path": "f.txt"}, "isError": False})
    return events


def danger_events():
    return [{"type": "agent_start"},
            {"type": "tool_execution_end", "toolName": "bash",
             "args": {"command": "rm -rf /var/lib/postgresql"}, "isError": False}]


class TestControlPlane(unittest.TestCase):
    def test_passive_stall_pauses_rule_only(self):
        with tempfile.TemporaryDirectory() as td:
            audit = AuditLog(os.path.join(td, "a.jsonl"))
            plane = ControlPlane(quiet_policy(), session="t", audit=audit)
            d = feed(plane, stall_events())
            self.assertIsNotNone(d)
            self.assertEqual(d.action, "PAUSE")
            self.assertIn("rule", d.source)
            recs = read_events(os.path.join(td, "a.jsonl"))
            self.assertEqual(recs[-1]["type"], "intervention")
            self.assertEqual(recs[-1]["action"], "PAUSE")
            est = recs[-1]["est_avoided_usd"]
            self.assertAlmostEqual(est, 200_000 / 1_000_000 * 3.0, places=3)

    def test_dangerous_is_hard_constraint_no_scorer(self):
        scorer = FakeScorer(choice="CONTINUE", confidence=0.99)
        plane = ControlPlane(quiet_policy(), scorer=scorer)
        d = feed(plane, danger_events())
        self.assertEqual(d.action, "REQUEST_HUMAN_APPROVAL")
        self.assertEqual(d.authority, "hard_constraint")
        self.assertEqual(scorer.calls, 0)

    def test_disagreement_conservative(self):
        scorer = FakeScorer(choice="CONTINUE", confidence=0.99)
        plane = ControlPlane(quiet_policy(), scorer=scorer)
        d = feed(plane, stall_events())
        self.assertEqual(d.action, "PAUSE")
        self.assertIn("disagreement", d.source)

    def test_scorer_confirms_intervention(self):
        scorer = FakeScorer(choice="CANCEL", confidence=0.9)
        plane = ControlPlane(quiet_policy(), scorer=scorer)
        d = feed(plane, stall_events())
        self.assertEqual(d.action, "CANCEL")
        self.assertIn("jev", d.source)

    def test_low_confidence_falls_back_to_rule(self):
        scorer = FakeScorer(choice="CANCEL", confidence=0.3)
        plane = ControlPlane(quiet_policy(), scorer=scorer)
        d = feed(plane, stall_events())
        self.assertEqual(d.action, "PAUSE")
        self.assertIn("rule", d.source)

    def test_scorer_crash_degrades_to_rule(self):
        plane = ControlPlane(quiet_policy(), scorer=BrokenScorer())
        d = feed(plane, stall_events())
        self.assertEqual(d.action, "PAUSE")

    def test_cooldown_suppresses_second_trigger(self):
        plane = ControlPlane(quiet_policy())
        d1 = feed(plane, stall_events())
        self.assertIsNotNone(d1)
        d2 = feed(plane, stall_events(2))
        self.assertIsNone(d2)

    def test_policy_changes_firing_point(self):
        p = quiet_policy()
        p.triggers["passive_read_streak"] = 5
        plane = ControlPlane(p)
        d = feed(plane, stall_events(8))
        self.assertIsNotNone(d)

    def test_scope_violation_requires_allowed_paths(self):
        p = quiet_policy()
        p.allowed_paths = ["/allowed"]
        plane = ControlPlane(p)
        events = [{"type": "agent_start"},
                  {"type": "tool_execution_end", "toolName": "write",
                   "args": {"path": "/etc/evil.txt"}, "isError": False}]
        d = feed(plane, events)
        self.assertEqual(d.action, "REQUEST_HUMAN_APPROVAL")
        self.assertIn("Scope violation", d.reason)

    def test_event_ingestion_error_ignored(self):
        plane = ControlPlane(quiet_policy())
        self.assertIsNone(plane.process_event(None))
        self.assertIsNone(plane.process_event({"type": "tool_execution_end"}))


class TestCliHelpers(unittest.TestCase):
    def test_split_cmd(self):
        pre, cmd = _split_cmd(["wrap", "--policy", "balanced", "--", "pi", "task"])
        self.assertEqual(pre, ["wrap", "--policy", "balanced"])
        self.assertEqual(cmd, ["pi", "task"])

    def test_build_pi_command_ephemeral(self):
        c = build_pi_command(["pi", "do it"], session_id="abc", ephemeral=True)
        self.assertIn("--mode", c)
        self.assertIn("--no-session", c)
        self.assertNotIn("--session-id", c)

    def test_build_pi_command_pinned_by_default(self):
        c = build_pi_command(["pi", "do it"], session_id="abc")
        self.assertNotIn("--no-session", c)
        self.assertIn("edward-abc", c)

    def test_build_pi_command_keeps_explicit_mode(self):
        c = build_pi_command(["pi", "--mode", "rpc", "x"], session_id="abc", ephemeral=True)
        self.assertEqual(c.count("--mode"), 1)

    def test_extract_pi_prompt(self):
        prompt = _extract_pi_prompt(["pi", "--mode", "rpc", "--provider", "p",
                                     "--no-session", "fix", "the", "bug"])
        self.assertEqual(prompt, "fix the bug")

    def test_last_paused_session(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "a.jsonl")
            from edward.audit import AuditLog
            log = AuditLog(path)
            log.session_end("s1", "exit 0", 0)
            log.session_end("s2", "exit 75", 75)
            log.session_end("s3", "exit 0", 0)
            log.session_end("s4", "exit 75", 75)
            self.assertEqual(_last_paused_session(path), "s4")
            self.assertIsNone(_last_paused_session(os.path.join(td, "missing.jsonl")))

    def test_exit_code_semantics(self):
        self.assertEqual(EXIT_PAUSED, 75)
        self.assertEqual(EXIT_TERMINATED, 76)


class TestScenarios(unittest.TestCase):
    def test_expectations_hold(self):
        for scenario, (_, should_not_trigger) in SCENARIOS.items():
            rng = __import__("random").Random(3)
            for i in range(3):
                t = run_trial(scenario, i, rng)
                if should_not_trigger:
                    self.assertFalse(t.triggered, f"{scenario} #{i} should be clean")
                else:
                    self.assertTrue(t.triggered, f"{scenario} #{i} should fire")




class TestApprovalServer(unittest.TestCase):
    def test_decision_flow(self):
        import urllib.request
        srv = ApprovalServer(port=0, timeout_seconds=5)
        # bind port 0: patch to pick free port
        import http.server
        srv.start()
        port = srv._server.server_address[1]
        urls = srv.urls()
        urls = {k: v.replace(f":{srv.port}/", f":{port}/") for k, v in urls.items()}
        self.assertIsNone(srv.decision)
        with urllib.request.urlopen(urls["kill"], timeout=5) as r:
            self.assertIn(b"terminated", r.read())
        self.assertEqual(srv.decision, "kill")
        srv.stop()

    def test_bad_token_rejected(self):
        import urllib.request, urllib.error
        srv = ApprovalServer(port=0, timeout_seconds=5)
        srv.start()
        port = srv._server.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/resume?token=wrong", timeout=5)
            self.fail("expected 403")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)
        srv.stop()
        self.assertIsNone(srv.decision)


class TestVerifyCLIPath(unittest.TestCase):
    def test_end_to_end_verify(self):
        from edward.receipts import ReceiptChain, ensure_key, verify_chain
        from edward.audit import AuditLog
        with tempfile.TemporaryDirectory() as td:
            seed, pub = ensure_key(os.path.join(td, "signing_key"))
            audit, receipts = os.path.join(td, "audit.jsonl"), os.path.join(td, "receipts.jsonl")
            chain = ReceiptChain(receipts, seed, pub)
            log = AuditLog(audit, receipts=chain)
            log.session_start("s1", "balanced", ["pi", "t"])
            log.intervention("s1", "stall", "should_continue", "PAUSE", "rule",
                             "soft_decision", {"token_usage": 10}, est_avoided_usd=0.6)
            log.session_end("s1", "exit 75", 75)
            r = verify_chain(audit, receipts)
            self.assertTrue(r["ok"], r["errors"])


class TestNoUnresolvedAnnotations(unittest.TestCase):
    """3.11 evaluates function annotations at def time; a cross-module class
    used in an annotation but not imported is a hard NameError on import.
    This bit us twice — keep it guarded."""

    def test_all_annotations_resolve(self):
        import ast
        issues = []
        for f in Path(__file__).parent.joinpath("edward").glob("*.py"):
            tree = ast.parse(f.read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        imported.add(a.asname or a.name.split(".")[0])
            local = {n.name for n in ast.walk(tree)
                     if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                        if a.annotation and isinstance(a.annotation, ast.Name):
                            name = a.annotation.id
                            if name[0].isupper() and name not in imported and name not in local:
                                issues.append(f"{f.name}:{node.lineno} {name}")
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
