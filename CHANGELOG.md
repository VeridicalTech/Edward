# Changelog

All notable changes to Edward are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is semver.

## [Unreleased]

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
