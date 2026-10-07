#!/usr/bin/env python3
"""StepShield holdout (216) — FULLY LOCAL stack: Ollama via the reference
scorer server (127.0.0.1:8001), probe v1b, asymmetric confirm.
This row = exactly what a user gets from the documented local setup.

Usage: bench_local_holdout.py [model_label] [express_gate]
  model_label   default qwen3.5:4b (also names the raw log)
  express_gate  optional: set the asymmetric express threshold (e.g. 0.98);
                default keeps the shipped gate (0.90)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import edward.stepshield as ss
from edward.stepshield import load_trajectories

label = sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:4b"
if len(sys.argv) > 2:
    ss.VIOLATION_HIGH_CONFIDENCE = float(sys.argv[2])
suffix = f"_hg{sys.argv[2]}" if len(sys.argv) > 2 else ""
tag = label.replace("qwen3.5:", "qwen").replace(":", "")

DATA = Path("/root/Edward-data/stepshield/data/test_holdout")
trajs = load_trajectories(DATA / "raw_trajectories.jsonl", data_dir=DATA)
rogue = sum(1 for t in trajs if t.trajectory_type == "rogue")
print(f"trajectories: {len(trajs)} (rogue {rogue}, clean {len(trajs)-rogue}) "
      f"modes: ['contract'] label: {label} express>={ss.VIOLATION_HIGH_CONFIDENCE}", flush=True)
out = open(f"/root/Edward/repo/results/raw/stepshield_contract_v1b_local_{tag}{suffix}.log",
           "w", buffering=1)
def log(msg):
    print(msg, flush=True)
    out.write(msg + "\n")
t0 = time.time()
suite = ss.evaluate_mode(trajs, "balanced", "contract",
                         scorer_base_url="http://127.0.0.1:8001",
                         log=log, confirm_mode="asymmetric", probe="v1b")
m = suite.compute()
log(f"RESULT local-{label}{suffix}: recall {m['recall']:.1%} fpr {m['fpr_clean']:.1%} "
    f"prec {m['precision']:.1%} eir3 {m['eir_3']:.3f} premature {m['premature']} "
    f"({time.time()-t0:.0f}s)")
log(f"RESULT by_cat: {m['by_category']}")
out.close()
print("DONE")
