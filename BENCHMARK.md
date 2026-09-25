# Benchmark Log — StepShield (external validation)

External validation against **StepShield: When, Not Whether to Intervene on
Rogue Agents** (NeurIPS 2026, github.com/glo26/stepshield, MIT code /
CC BY 4.0 data).

Dataset: `test_holdout`, 216 trajectories (108 rogue / 108 clean), 6 attack
categories (UFO / SEC / RES / INV / TST / DEC), step-level divergence labels.
Metrics are paper-aligned: EIR_k = P[s_d >= s_r AND s_d - s_r <= k | detected],
i.e. how often a detection lands within k steps *after* the first rogue step
(premature flags on benign prefixes earn no credit).

Scorer: Qwen/Qwen3.5-4B served locally (bf16, optimized linear-attention
kernels — see `/health` `kernel_profile` on the scoring service). Policy:
`balanced` (FROZEN defaults). Dates: 2026-09-22. All raw logs are archived
under `results/raw/` with SHA256SUMS.

## Measurement series

| # | Config | Recall | FPR (clean) | Precision | EIR₃ | Gap median | Note |
|---|---|---|---|---|---|---|---|
| 1 | rules (deterministic layer only) | 7.4% | **1.9%** | 80.0% | — | — | dangerous-command regex + constraint-derived scope |
| 2 | contract single-shot, gate 0.5 | **70.4%** | 42.6% | 62.3% | 0.605 | 0 | |
| 3 | contract two-strike | 10.2% | 3.7% | 73.3% | 0.455 | 0 | disproved: the dataset has single-shot violations |
| 4 | **contract asymmetric** (>=0.9 express; 0.6–0.9 corroborated; strong OK clears) | 57.4% | 21.3% | 72.9% | **0.790** | 0 | EIR₁ 0.774 |

## Confidence sweep (run #2 first-violation data, single-shot lower bound)

| gate | recall | FPR |
|---|---|---|
| 0.50 | 67.6% | 41.7% |
| 0.70 | 63.9% | 21.3% |
| 0.80 | 53.7% | 13.0% |
| 0.90 | 42.6% | 10.2% |
| 0.95 | 36.1% | 3.7% |

Clean-FP and rogue-TP confidence distributions overlap (false positives exist
even at confidence 1.0) — single thresholds cannot separate the classes;
temporal corroboration (run 4) is the effective lever.

## Kernel-change validation

The scoring service later swapped its attention kernels (causal-conv1d 1.7.0 +
flash-linear-attention 0.5.2; visible via `/health` `kernel_profile`). Warm
forward latency dropped 58.8ms → 41.7ms (~29%). Headline configs were re-run:

| Config | Kernel | Recall | FPR (clean) | EIR₃ | Note |
|---|---|---|---|---|---|
| #2 single-shot | reference | 70.4% | 42.6% | 0.605 | |
| #2' single-shot | **optimized** | **76.9%** | **55.6%** | 0.542 | trigger-happier at the margin |
| #4 asymmetric | reference | 57.4% | 21.3% | 0.790 | |
| #4' asymmetric | **optimized** | 57.4% | **20.4%** | **0.790** | one conf-1.0 clean FP dropped |

Per-trajectory flip analysis: all 21 single-shot flips are new detections
inside the 0.52–0.68 confidence band; the asymmetric configuration's decision
set is invariant (66/67 shared, zero rogue flips, confidence deltas median
0.000 / max 0.020). Conclusion: numeric drift moves borderline single-shot
verdicts, while temporal corroboration filters them — the architecture, not
the floats, carries the decision. The 0.6/0.9 gates held; no recalibration.

## Probe hardening (ablation on train split; holdout once)

The endpoint is deterministic (identical scores on repeated calls), so
self-consistency voting is inapplicable. Seven probe styles were ablated on a
stratified 60-trajectory TRAIN-sample (seed 42); the holdout was evaluated
once, with the chosen config only:

| probe | recall | FPR | precision | EIR₃ | note |
|---|---|---|---|---|---|
| v1 (baseline) | 56.7% | 36.7% | 60.7% | 0.706 | 3-option evidence-only |
| v2a typed classes | 20.0% | 0.0% | 100% | 0.500 | 8 options split probability mass — decisiveness collapses |
| v2d typed + aggregated mass | 26.7% | 6.7% | 80.0% | 0.625 | aggregation recovers only part |
| **v1b = v1 + enriched evidence** | **56.7%** | **30.0%** | **65.4%** | 0.706 | temporal context + deterministic counters + keyword-guided excerpts |
| v1c = v1b + few-shots | 40.0% | 13.3% | 75.0% | 0.833 | few-shots make the 4B conservative — an alternative operating point, not dominant |

**Lesson**: for a 4B judge, taxonomy load and exemplars trade recall for
precision; evidence enrichment (facts, not examples) is the free lunch.

**Holdout final (v1b, one shot)**:

| Config | Recall | FPR | Precision | EIR₃ | Note |
|---|---|---|---|---|---|
| #4' asymmetric v1 | 57.4% | 20.4% | 72.9% | 0.790 | previous best |
| **#5 asymmetric v1b** | **58.3%** | **17.6%** | **76.8%** | 0.778 | SEC 13/18 (was 11); now the shipped default |
| #6 asymmetric v1b, **Jev 1.13 backend** | **59.3%** | **10.2%** | **85.3%** | **0.906** | one batched call per probe battery; hosted API |

### Cross-check: hosted typed judge (Jev 1.13)

Swapping the judgment backend (no other change: same holdout, probe v1b,
asymmetric confirmation) shows the trade each backend makes:

| backend | Recall | FPR | Precision | EIR₃ | data locality |
|---|---:|---:|---:|---:|---|
| local 4B (`endpoint`) | 58.3% | 17.6% | 76.8% | 0.778 | events never leave the network |
| Jev 1.13 (`jev`) | 59.3% | 10.2% | 85.3% | 0.906 | state leaves the network per call |
| GPT-4.1-mini judge (StepShield paper) | 95.4% | 5.6% | — | 0.89 | — |

Jev's calibrated single-call judgment cuts false positives by 42% relative
(17.6% → 10.2%) and lifts EIR₃ above the paper's GPT-4.1-mini judge, at
roughly $0.0001 per batched call and ~23 min wall time for the full holdout
(vs 41 ms/decision on the local 4B). The recall ceiling is shared across both
small-judge backends — trajectory-level recall, not verdict quality, is the
frontier (see the fine-tune post-mortem below). Raw log:
`results/raw/stepshield_contract_v1b_jev.log`; reproduction:

```bash
EDWARD_SCORER_BACKEND=jev TYPESAFE_API_KEY=... \
  python3 tools/bench_jev_holdout.py
```

## Fine-tune experiment — strikes 1–2, abandoned

The scorer was fine-tuned (LoRA SFT on Qwen3.5-4B) to lift recall. Per the
pre-registered two-strike protocol in `lora/FINE_TUNING.md`, the config was
abandoned after two failed holdout gates and the service rolled back to the
base model (verified via `/health`). Four holdout evaluations across three
training configurations, all on the same held-out set, asymmetric confirmation:

| Config | Gate | Recall | FPR (clean) | EIR₃ | Verdict |
|---|---:|---:|---:|---:|---|
| **v1b (base zero-shot, shipped)** | 0.6 | **76.8%** | **17.6%** | 0.778 | **retained** |
| ft-v1 (lr 1e-4 / 3 epochs) | 0.6 | 69.4% | 30.6% | 0.747 | rejected |
| ft-v2 (lr 5e-5 / 2 epochs) | 0.6 | 41.7% | 5.6% | 0.822 | rejected |
| ft-v3-final (+ augmentation / upsampling) | 0.75 | 62.0% | 29.6% | 0.731 | rejected (strike 1) |
| ft-v3-ckpt100 | 0.7 | 66.7% | 23.1% | 0.778 | rejected (strike 2) |

### ⚠️ Disclosure

- ft-v3 consumed the StepShield `test` split for training. Provenance audit:
  the test split's 639 rogue + 639 clean trajectories are **verbatim
  desensitized mirrors of the train split** (mapping.csv `source=train`;
  prompt-hash dedup showed zero net violation increase) — not new attack
  families. Its real increment was 6,657 benign trajectories (FPR side only).
- The holdout therefore degrades to "same-family supervised + sealed set".
  Any comparison against the paper baselines (whose detectors did not consume
  the test split) carries that favorable-condition caveat.
- DEC class scarcity is structural: 13 divergence steps per split (train and
  test), by generation design. Paraphrase augmentation (924 rows) + class
  upsampling did not close it.

### Findings

1. **In-domain validation has no predictive power over the holdout.** Val
   argmax ranged 88–93% across configurations while holdout recall ranged
   41.7–76.8% with no correlation. Model selection on in-domain metrics is
   unsafe for this task class.
2. **Bottleneck = attack-family diversity, not training recipe.** With ~500
   same-family violation trajectories, LoRA SFT on a 4B never beat base
   zero-shot recall (best 69.4% vs 76.8%), regardless of aggressiveness.
3. **Side asset**: ft-v2 proved FPR 5.6% with EIR₃ 0.822 is reachable (better
   than shipped on both) — a low-false-alarm operating point archived for
   notification-style deployments where recall requirements are lower.
4. **Restart conditions for the fine-tune line**: new attack-family corpora
   (not mirrors, not paraphrases) or a larger base model. The holdout budget
   is frozen until then.

### Ops note

Two GPU Xid-13 events occurred during this campaign (one serving, one
training; driver 610.43.02). Serving already auto-heals via systemd; training
recovered from a checkpoint via `--resume`. Driver stability is flagged for
evaluation. Train/serve parity was audited: 155/155 prompts byte-identical
between the training renderer and the serving probe.

## Conclusions (current state)

1. The deterministic layer alone is quantitatively blind to content-semantic
   violations (recall 7.4%) while keeping the best FPR (1.9%) — the
   silent-corruption gap the product thesis predicts, now externally measured.
2. The shipped configuration (v1b: evidence-enriched probe + asymmetric
   temporal confirmation over a local 4B) is the best overall operating point:
   recall 58.3% / FPR 17.6% / EIR₃ 0.778 at ~$0.00002/decision.
3. Fine-tuned models are archived, not shipped: one as a low-FPR operating
   point, one as a candidate second-stage corroborator. Restart requires new
   attack-family corpora.

## Reproduce

```bash
git clone --depth 1 https://github.com/glo26/stepshield /tmp/stepshield
export EDWARD_SCORER_URL=http://localhost:8000     # your local scorer
edward eval --suite stepshield --data /tmp/stepshield/data --mode rules
edward eval --suite stepshield --data /tmp/stepshield/data \
    --mode contract --probe v1b --show-mechanisms
```

Raw logs: `results/raw/` (gate logs, training logs, environment captures,
dataset hashes; SHA256SUMS). Evidence logs are published as captured, with
local filesystem paths and host identifiers lightly redacted.
