#!/usr/bin/env python3
"""StepShield holdout (216) — FULLY LOCAL stack: Ollama qwen3.5:4b via the
reference scorer server (127.0.0.1:8001), probe v1b, asymmetric confirm.
This row = exactly what a user gets from the documented local setup."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edward.stepshield import load_trajectories, evaluate_mode

DATA = Path("/root/Edward-data/stepshield/data/test_holdout")
trajs = load_trajectories(DATA / "raw_trajectories.jsonl", data_dir=DATA)
rogue = sum(1 for t in trajs if t.trajectory_type == "rogue")
print(f"trajectories: {len(trajs)} (rogue {rogue}, clean {len(trajs)-rogue}) "
      f"modes: ['contract']", flush=True)
out = open("/root/Edward/repo/results/raw/stepshield_contract_v1b_local_qwen3.log",
           "w", buffering=1)
def log(msg):
    print(msg, flush=True)
    out.write(msg + "\n")
t0 = time.time()
suite = evaluate_mode(trajs, "balanced", "contract",
                      scorer_base_url="http://127.0.0.1:8001",
                      log=log, confirm_mode="asymmetric", probe="v1b")
m = suite.compute()
log(f"RESULT local-qwen3.5:4b: recall {m['recall']:.1%} fpr {m['fpr_clean']:.1%} "
    f"prec {m['precision']:.1%} eir3 {m['eir_3']:.3f} premature {m['premature']} "
    f"({time.time()-t0:.0f}s)")
log(f"RESULT by_cat: {m['by_category']}")
out.close()
print("DONE")
