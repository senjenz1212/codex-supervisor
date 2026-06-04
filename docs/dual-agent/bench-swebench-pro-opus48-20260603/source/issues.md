# Issue Slice Pack

## Slice 1 - SWE-bench Pro pilot sample and report builder

Priority: P1

PRD promises: P3, P4, P5.

Scope: Add `supervisor/swe_bench_eval.py` with schema-versioned sample/results
loaders, pass@1/pass@5/pass^5 metrics, delta CI, noise-floor verdict, replay
export, and a pilot-plan builder.

Acceptance criteria:
- [ ] Fixed seed/sample loads from `tests/fixtures/swe_bench_pro/pilot_sample.yaml`.
- [ ] Report computes per-arm metrics and delta/noise-floor verdict.
- [ ] Report-only flags remain false for config and policy mutation.

## Slice 2 - Harness and baseline solver adapters

Priority: P1

PRD promises: P1, P2, P5.

Scope: Add `supervisor/swe_bench_solver.py` with a codex-supervisor harness
adapter, a mini-swe-agent baseline plan adapter, a diff-to-`model_patch` JSONL
writer, and an evaluator-compatible patch exporter.

Acceptance criteria:
- [ ] Harness adapter captures a git diff as `{instance_id, model_patch}`.
- [ ] Empty or missing instance ids are rejected.
- [ ] Baseline and harness plans carry the same model and budget.

## Slice 3 - Pilot CLI

Priority: P2

PRD promises: P2, P4, P5.

Scope: Add `scripts/run_swe_bench_pro_pilot.py` to write dry-run plans, generate
fixture-backed reports, and refuse live execution without an explicit budget.

Acceptance criteria:
- [ ] CLI returns rc 2 when live mode lacks `--allow-live` or budget.
- [ ] Dry-run writes `pilot-plan.json`.
- [ ] Replay mode writes `report.json` and `rows.jsonl`.

## Slice 4 - Tests and deterministic fixtures

Priority: P1

PRD promises: P1-P5.

Scope: Add `tests/test_swe_bench_pro_eval.py` plus
`tests/fixtures/swe_bench_pro/{pilot_sample.yaml,pilot_results.json}`.

Acceptance criteria:
- [ ] Tests cover the public boundaries in the TDD plan.
- [ ] No test calls live models, Docker, Modal, HuggingFace, or GitHub.
- [ ] `supervisor/config.py` and `supervisor/state.py` remain untouched.
