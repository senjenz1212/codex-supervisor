# TDD Grill Findings

### Finding 1: Tests must not assert only helper internals

Status: resolved

Resolution: Each test names a public boundary: sample loader, report builder,
solver adapter, pilot plan, or pilot CLI. Helper math is exercised through the
report builder, not directly.

### Finding 2: The model route needs a regression assertion

Status: resolved

Resolution: `test_swe_bench_pilot_plan_uses_same_model_budget_and_opus48_route`
must assert `CLAUDE_OPUS_UNDERLYING_MODEL == "claude-opus-4-8"` and that the
pilot plan records the same fixed model for both arms.

### Finding 3: Dry-run scaffolding could be mistaken for a live pilot

Status: resolved

Resolution: CLI tests must prove live execution is refused without explicit
`--allow-live` and `--max-budget-usd`, and final docs must label the committed
report as replay/fixture-backed.

### Finding 4: Evaluator shape mismatch can silently break grading

Status: resolved

Resolution: The solver tests must cover solver JSONL with `model_patch`; the
adapter also provides evaluator-compatible rows with `patch` and `prefix` for
SWE-bench Pro's gather/eval path.

### Finding 5: Defaults can drift accidentally

Status: resolved

Resolution: The report-only test compares `AgenticLeadCfg().model_dump()`
before and after report construction and the supervised workflow runs with
per-call `agentic_lead_policy=off`.
