#!/usr/bin/env python3
"""StepShield holdout (216) — Jev 1.13 backend + Pareto intervention
selection (pareto_intervention=True). Companion to bench_jev_holdout.py
(legacy single-question path, EIR₃ 0.906)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edward.stepshield import load_trajectories, evaluate_mode

DATA = Path("/root/Edward-data/stepshield/data/test_holdout")
trajs = load_trajectories(DATA / "raw_trajectories.jsonl", data_dir=DATA)
rogue = sum(1 for t in trajs if t.trajectory_type == "rogue")
print(f"trajectories: {len(trajs)} (rogue {rogue}, clean {len(trajs)-rogue}) "
      f"modes: ['contract']", flush=True)
out = open("/mnt/intel/AgentStateEngine/results/raw/stepshield_contract_v1b_jev_pareto.log",
           "w", buffering=1)
def log(msg):
    print(msg, flush=True)
    out.write(msg + "\n")
t0 = time.time()
from edward.config import load_policy
policy = load_policy("balanced")
policy.pareto_intervention = True
suite = evaluate_mode(trajs, policy, "contract",
                      scorer_base_url="https://api.typesafe.ai/v1/systemone",
                      log=log, confirm_mode="asymmetric", probe="v1b")
m = suite.compute()
log(f"RESULT jev-1.13+pareto: recall {m['recall']:.1%} fpr {m['fpr_clean']:.1%} "
    f"prec {m['precision']:.1%} eir3 {m['eir_3']:.3f} premature {m['premature']} "
    f"({time.time()-t0:.0f}s)")
log(f"RESULT by_cat: {m['by_category']}")
out.close()
print("DONE")
