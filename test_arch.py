"""Architecture hardening tests: process-tree termination, unresolved
scorer semantics, replay determinism, agent adapter registry."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from edward.adapters import GenericAdapter, PiAdapter, resolve_adapter
from edward.audit import AuditLog, summarize
from edward.backends import DisabledBackend
from edward.config import Policy
from edward.engine import ControlPlane
from edward.procmgmt import spawn, terminate_tree
from edward.scorer import Scorer


def stall_events(n_reads=14):
    events = [{"type": "agent_start"}]
    for _ in range(n_reads):
        events.append({"type": "turn_start"})
        events.append({"type": "tool_execution_end", "toolName": "read",
                       "args": {"path": "f.txt"}, "isError": False})
    return events


class TestTerminateTree(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "POSIX process groups")
    def test_kills_full_tree_promptly(self):
        proc = spawn(["sh", "-c", "sleep 30 & sleep 30 & wait"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertIsNone(proc.poll())
        t0 = time.time()
        terminate_tree(proc, grace=3.0)
        dt = time.time() - t0
        self.assertIsNotNone(proc.poll(), "process must be dead")
        self.assertLess(dt, 6.0, "graceful→forced escalation must stay prompt")

    def test_terminate_on_dead_process_is_noop(self):
        proc = spawn(["true"])
        proc.wait()
        terminate_tree(proc)  # must not raise


class TestUnresolvedScorer(unittest.TestCase):
    def test_unavailable_scorer_marks_audit_record(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audit.jsonl")
            plane = ControlPlane(Policy(), session="s", audit=AuditLog(path),
                                 scorer=Scorer(backend=DisabledBackend()))
            decision = None
            for ev in stall_events():
                decision = plane.process_event(ev)
                if decision:
                    break
            self.assertIsNotNone(decision, "stall trigger must fire")
            self.assertEqual(decision.action, "PAUSE")
            d = summarize(path)
            self.assertEqual(d["interventions"], 1)
            self.assertEqual(d["unresolved_interventions"], 1)

    def test_no_scorer_configured_is_not_unresolved(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audit.jsonl")
            plane = ControlPlane(Policy(), session="s", audit=AuditLog(path),
                                 scorer=None)
            for ev in stall_events():
                plane.process_event(ev)
            d = summarize(path)
            self.assertEqual(d["interventions"], 1)
            self.assertEqual(d["unresolved_interventions"], 0)


class TestReplayDeterminism(unittest.TestCase):
    def _feed(self, offset):
        t = [1000.0 + offset]

        def clock():
            t[0] += 7.0
            return t[0]

        plane = ControlPlane(Policy(), session="r", clock=clock)
        decisions = []
        for ev in stall_events():
            d = plane.process_event(ev)
            if d:
                decisions.append((d.action, d.source, d.decision_type))
        summary = plane.summary()
        return decisions, summary

    def test_same_events_same_outcome_under_shifted_clock(self):
        d1, s1 = self._feed(0.0)
        d2, s2 = self._feed(987654.0)
        self.assertTrue(d1, "trigger must fire under scripted clock")
        self.assertEqual(d1, d2, "decisions must not depend on wall clock")
        self.assertEqual(s1["turn_count"], s2["turn_count"])
        self.assertEqual(s1["total_tool_calls"], s2["total_tool_calls"])
        self.assertEqual(s1["interventions"], s2["interventions"])


class TestAdapterRegistry(unittest.TestCase):
    def test_auto_is_generic_for_non_pi_cmd(self):
        self.assertIsNone(resolve_adapter("auto", ["python", "x.py"]))

    def test_auto_picks_pi_for_pi_cmd(self):
        if PiAdapter.is_available():
            self.assertIs(resolve_adapter("auto", ["pi", "task"]), PiAdapter)
        else:
            self.assertIsNone(resolve_adapter("auto", ["pi", "task"]))

    def test_generic_explicit(self):
        self.assertIsNone(resolve_adapter("generic", ["pi", "task"]))

    def test_unknown_agent_exits_with_available_list(self):
        with self.assertRaises(SystemExit) as cm:
            resolve_adapter("does-not-exist", ["x"])
        self.assertIn("generic", str(cm.exception))

    def test_generic_adapter_passthrough(self):
        cmd = ["python", "-m", "agent"]
        self.assertEqual(GenericAdapter.build_command(cmd), cmd)
        self.assertTrue(GenericAdapter.matches(cmd))


if __name__ == "__main__":
    unittest.main()
