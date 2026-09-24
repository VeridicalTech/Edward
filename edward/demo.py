"""edward demo: self-running proof that the control plane works.

Replays built-in failure scenarios through the full ControlPlane and
prints detection + intervention timing. Offline by default; pass
--scorer to include live semantic scoring.
"""

import sys
import time

from .config import load_policy
from .engine import ControlPlane  # noqa: F401
from .scorer import make_scorer
from .evalcmd import eval_policy


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def run_demo(n_trials: int = 3, policy_source: str = None, use_scorer: bool = False) -> int:
    policy = load_policy(policy_source)
    scorer = None
    if use_scorer:
        scorer = make_scorer(policy)
        health = scorer.health()
        if health and health.get("ready"):
            print(f"scorer: {health.get('model')} ready")
        else:
            print(f"{_c('33', 'scorer unreachable — running rule-only demo')}")
            scorer = None

    print(f"policy: {policy.preset}  trials/scenario: {n_trials}\n")
    header = f"{'scenario':<20} {'expect':<10} {'result':<12} {'latency':>8}"
    print(header)
    print("-" * len(header))

    t0 = time.time()
    metrics = eval_policy(policy, n_trials=n_trials, scorer=scorer, seed=7)
    ok = True
    for scenario, m in metrics.items():
        expect = "no-fire" if m["type"] == "normal" else "fire"
        if m["type"] == "normal":
            result = "clean" if m["fp"] == 0 else f"{m['fp']} FP"
            good = m["fp"] == 0
            latency = "—"
        else:
            result = f"{m['detection_rate']:.0%} detected"
            good = m["detection_rate"] == 1.0
            latency = f"{m['avg_latency']:.1f}"
        ok = ok and good
        mark = _c("32", "ok") if good else _c("31", "FAIL")
        print(f"{scenario:<20} {expect:<10} {result:<12} {latency:>8}  {mark}")

    print(f"\n{'PASS' if ok else 'FAIL'} in {time.time()-t0:.1f}s "
          f"(deterministic rules frozen defaults; scorer {'live' if scorer else 'off'})")
    return 0 if ok else 1
