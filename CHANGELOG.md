# Changelog

All notable changes to Edward are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is semver.

## [0.4.2] - 2026-10-08

### Added
- **Native `claude` and `codex` adapters**: `edward wrap --agent claude -- claude -p ...`
  and `--agent codex` now translate each CLI's JSONL stream (stream-json /
  exec --json, plus on-disk rollout schema) into canonical control-plane
  events — tool calls, retries, session lifecycle — verified against live
  CLI output. A fake-agent E2E shows a `rm -rf` inside a claude stream being
  intercepted and terminated (exit 76).
- **`--style vllm` scorer direct readout**: raw `/v1/completions` with
  `allowed_token_ids` pinned to the option letters and `logprobs=20`;
  letters outside the top-k are fetched with a constrained follow-up
  request, so every probability is a raw logprob (strongest local readout).
- `edward init --scorer ollama`: writes the policy with the local scorer URL
  and prints the three-command bring-up recipe.
- `edward doctor --probe`: one real scored round-trip after the health check.

### Fixed
- `edward doctor --probe` scorer API mismatch (found by a fresh-venv
  install test).
- Windows key-file permission test (NTFS has no POSIX bits) — found by
  widening CI to full test discovery across 3 OSes x Python 3.11-3.13.

## [0.4.1] - 2026-10-07

### Fixed
- **Contract evidence renderer**: path-only write/edit traces (no diff text
  recorded) were rendered as `content: ''`, which small models read as a file
  wipe — a systematic DESTRUCT false-positive class (local bench FPR 40.7%).
  Now rendered explicitly as "diff content not recorded in trace".

### Changed
- **`edward-scorer-server --style ollama`**: SemIf-style direct readout
  (TheoLeeCJ/SemIf direct-options) — raw ChatML with an empty think block,
  one forward pass, no generated text; the decision distribution is softmaxed
  from the top-20 option-letter logprobs in float64. ~0.2 s/decision with
  real calibrated probabilities (was: 30-60 s thinking or fabricated one-hot
  confidence). The asymmetric-confirm gates now operate on live probability.
- scorer server max_tokens 2048 -> 4096 on the openai style (thinking models
  exhausted the budget before emitting content; verdicts were being harvested
  from truncated reasoning).

### Measured
- Fully-local holdout rows added to BENCHMARK.md: Ollama qwen3.5:9b direct
  readout lands within noise of the tuned LAN 4B service (56.5%/19.4% vs
  58.3%/17.6% recall/FPR); recommended express gate 0.98 gives 44.4% recall
  at 7.4% FPR, 85.7% precision, EIR3 0.833, ~25 min wall on an RTX 3070 Ti.

## [0.4.0] - 2026-09-25

### Added
- **`edward init`**: three answers (agent / scenario / scorer) -> starter
  policy file + next commands. CI scenarios get conservative triggers and a
  `done_checks` starter; kills the policy-authoring cognitive tax.
- **`edward bench`**: honest self-overhead — ~0.9 µs/event ingest (1.06M
  events/s), trigger evaluation p95 ~3 µs on the rules-only path. The
  guardrail that publishes its own overhead.
- **`--emit-feedback FILE`** (TS-Flow pattern): on intervention, write a
  structured feedback JSON (action, reason, spend, suggested next) for the
  agent or harness to read on resume — guardrail verdicts feed the agent
  instead of a bare exit code.
- **Prediction-gated deferral — the WAIT action** (policy `deferral_enabled`,
  opt-in): when a soft trigger fires, one batched scorer call asks whether
  waiting one horizon converges without harm; if so Edward defers (bounded by
  `deferral_max_waits`, re-check after `deferral_horizon_seconds`), and every
  WAIT is audited with its prediction. Failing predictions and exhausted
  deferral budgets force PAUSE; hard constraints never defer. (JevTree's
  receding-horizon idea applied to the supervisor.)
- **Reference scorer server** (`python -m edward.scorer_server`): the missing
  half of the local-scorer story — expose Edward's `/v1/score` contract over
  any OpenAI-compatible endpoint (Ollama, vLLM, llama.cpp). Zero dependencies;
  `edward doctor` points at it when the scorer is unreachable. Reports
  one-hot probabilities (a plumbing reference, not a calibrated judge —
  labeled as such).
- **Verification-gated convergence (`--done-check`)**: declare shell commands
  that verify task completion (policy `done_checks` or repeatable CLI flag).
  A passing check suppresses the convergence-stall PAUSE (recorded as
  `done_verified` in the audit); a failing one becomes evidence in the
  intervention reason — premature-done detection comes free. (JevTree's
  terminal-verifier idea, adapted.)
- **Pareto intervention selection** (policy `pareto_intervention`, opt-in):
  when a soft trigger fires, one batched scorer call evaluates recovery
  probability per candidate action (continue/pause/cancel) plus risk of
  inaction; the kernel composes deterministically (least-severe recovering
  action; safety-first CANCEL when nothing recovers). The model supplies
  calibrated probabilities; code owns thresholds. Off by default until
  benchmarked on the same holdout.
- **Scorer coverage audit**: sessions now record `scorer_stats` (tool calls
  seen vs scorer consultations vs failures) and `edward audit --json` reports
  a `coverage` block — answering "what did the guard actually see?"
- `edward mcp-proxy` design sketch posted on issue #3 (transport and
  enforcement planes specified; implementation to follow).

## [0.3.0] - 2026-09-23

### Added
- `--json` flag for `edward audit`: emits the summarize() dict as JSON for CI
  badges and fleet polling; `--tail` keeps raw-line output (community PR #5,
  thanks @RugvedBane).
- `edward demo --offline` (with `--live-scorer`): full rules+scorer pipeline
  against a deterministic heuristic stub — no GPU, no API key.
- Agent adapter registry (`edward/adapters.py`): `edward wrap --agent
  auto|generic|pi|<plugin>`; third-party adapters plug in via the
  `edward.adapters` entry-point group. Pi helpers moved out of the CLI
  module (re-exported for API compatibility).
- Unresolved scorer consultations are now recorded: when a deterministic
  trigger fires but the scorer yields no verdict, the audit record carries
  `jev.unresolved = "scorer_unavailable"` and `edward audit` reports
  `unresolved_interventions` — rule action stands, evidence is kept.
- Injectable clock in `ControlPlane`/`StateEngine`; replay-determinism test
  pins decision outcomes to event content, not wall clock.

### Fixed
- Interventions now terminate the **whole agent process tree** (POSIX
  process groups + SIGTERM→SIGKILL escalation; Windows
  CREATE_NEW_PROCESS_GROUP + CTRL_BREAK_EVENT → `taskkill /T /F`) —
  previously only the direct child was killed, orphaning agent subprocesses.
- `edward verify` scans rotated audit generations (`.1`–`.3`): a recent
  rotation no longer produces false "record absent" warnings; true
  deletions still warn.

### Added
- Pluggable scorer backends (`edward/backends.py`): `EDWARD_SCORER_BACKEND`
  selects `endpoint` (default LAN `/v1/score` server), `jev` (TypeSafe Jev —
  all probes batched into one calibrated `/v1/systemone` call, confidence
  surfaced for routing), or `heuristic` (deterministic marker stub for
  offline demos/tests). `edward doctor` shows the active backend. Zero new
  dependencies; tests run fully offline via injected transports.

### Changed
- StepShield contract probe default is now `v1b` (evidence enrichment:
  temporal context, deterministic counters, keyword-guided excerpts);
  holdout: recall 57.4%→58.3%, FPR 20.4%→17.6%, precision 72.9%→76.8%.
  Probe styles v1c/v2a–v2d available for study; see BENCHMARK.md.

## [0.2.0] - 2026-09-22

### Added
- **Signed evidence receipts**: every audit record is Ed25519-signed into a
  hash chain (`edward/receipts.py`, pure stdlib, verified against RFC 8032
  test vectors); `edward keygen` and `edward verify` for offline tamper
  checks. Matching is by record hash — rotation-safe.
- **Human approval loop**: `edward wrap --wait-approval N` sends Resume/Kill
  decision links (Slack incoming webhook or stderr) and waits; PAUSE becomes
  an interactive decision with session pinning intact.
- Terminal demo GIFs (real transcripts) rendered via
  `tools/make_cast.py` + agg; embedded in README / README.zh-CN.md.
- Chinese README (`README.zh-CN.md`).

## [0.1.1] - 2026-09-22

### Added
- $ saved estimation: interventions record est. avoided spend (burn-to-budget
  model); `edward audit` aggregates it
- `EDWARD_SCORER_URL` environment override for the scorer endpoint
- Deployment templates for team-LAN topology (`deploy/`: Dockerfile,
  docker-compose.yml, runbook)

### Changed
- PyPI distribution name is `edward-guard` (the `edward` name is taken);
  brand, package import, and CLI remain `edward`

## [0.1.0] - 2026-09-22

First public release.

### Added
- `edward` CLI: `wrap` / `demo` / `eval` / `audit` / `doctor` / `policy-template`
- External control plane: canonical event schema (pi / Codex / custom agents),
  cross-turn StateEngine, 8 trigger rules (FROZEN defaults, policy-parameterized)
- Semantic scorer client for any OpenAI-compatible local endpoint, with a
  circuit breaker (rule-only fallback when the scorer is down)
- Evidence-grounded task-contract probe mode (StepShield integration)
- Policy packs: TOML/JSON, three presets (conservative / balanced / aggressive)
- Audit JSONL with rotation; PAUSE/resume semantics (exit 75, session pinning,
  `--continue` from audit); exit codes 0 / 75 / 76 / 130
- Scenario suite + policy evaluation gate (`edward demo`, `edward eval`)
- StepShield (NeurIPS 2026) benchmark adapter with paper-aligned EIR metrics
- Product test suite (42 offline tests) and CI matrix (linux/windows/macos)
