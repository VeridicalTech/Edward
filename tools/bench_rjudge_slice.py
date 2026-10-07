#!/usr/bin/env python3
"""R-Judge terminal slice (early signal, n=21): records from Lordog/R-Judge
whose agent actions include TerminalExecute, mapped to Edward run_command
events. Labels: 1=unsafe (rogue), 0=safe (clean). Requires the scorer
server at 127.0.0.1:8001. R-Judge data: git clone -q --depth 1
https://github.com/Lordog/R-Judge /root/Edward-data/rjudge"""
import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/root/Edward/repo")
from edward.stepshield import StepTrajectory, evaluate_mode

RJ = Path("/root/Edward-data/rjudge/data")


def tool_of(a):
    m = re.match(r"\s*([A-Za-z_]+)\s*:", a or "")
    return m.group(1) if m else None


def load_slice():
    trajs = []
    for f in sorted(glob.glob(str(RJ / "*/*.json"))):
        cat = Path(f).parent.name
        for r in json.load(open(f)):
            steps, task = [], ""
            for turn in r["contents"]:
                for msg in turn:
                    role = msg.get("role")
                    content = msg.get("content") or ""
                    if role == "user" and not task:
                        task = content[:160]
                    if role == "agent" and msg.get("action") \
                            and tool_of(msg["action"]) == "TerminalExecute":
                        try:
                            arg = json.loads(msg["action"].split(":", 1)[1])
                        except json.JSONDecodeError:
                            arg = {}
                        steps.append({"step": len(steps) + 1, "action": "run_command",
                                      "arguments": {"command": str(arg.get("command", ""))[:1500]},
                                      "observation": "", "thought": (msg.get("thought") or "")[:200]})
            if steps:
                trajs.append(StepTrajectory(
                    trajectory_id=f"rjudge-{r['id']}",
                    task_title=task or f"record-{r['id']}",
                    description=(r.get("risk_description") or "")[:200],
                    steps=steps,
                    trajectory_type="rogue" if r["label"] == 1 else "clean",
                    category=f"rjudge-{cat}"))
    return trajs


if __name__ == "__main__":
    trajs = load_slice()
    print(f"rjudge terminal slice: {len(trajs)} records "
          f"(unsafe {sum(t.trajectory_type == 'rogue' for t in trajs)})", flush=True)
    suite = evaluate_mode(trajs, "balanced", "contract",
                          scorer_base_url="http://127.0.0.1:8001",
                          log=lambda m: print(m, flush=True),
                          confirm_mode="asymmetric", probe="v1b")
    m = suite.compute()
    print(f"RESULT rjudge-slice: recall {m['recall']:.1%} fpr {m['fpr_clean']:.1%} "
          f"prec {m['precision']:.1%}")
