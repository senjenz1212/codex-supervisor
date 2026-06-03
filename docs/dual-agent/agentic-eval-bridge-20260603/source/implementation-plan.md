# Implementation Plan: Agentic Eval Bridge

## Files / Modules To Touch

- `supervisor/agentic_eval_assembler.py`
- `scripts/run_agentic_eval_live.py`
- `tests/test_agentic_eval_bridge.py`
- `tests/fixtures/agentic_eval/agentic_lead_bridge_dataset.yaml`
- `tests/fixtures/agentic_eval/bridge_cassettes/*.json`
- `docs/dual-agent/agentic-eval-bridge-20260603/test-evidence.md`

## Steps

1. Add assembler dataclasses/helpers for mode mapping, budget split metadata,
   workflow-result normalization, verdict evidence extraction, cassette writes,
   and dataset export.
2. Add an opt-in live CLI that selects a subset from the curated labeled set,
   constructs a workflow runner around the existing workflow CLI, records arm
   cassettes, writes the assembled dataset, and exports the replay report.
3. Add replay fixtures and tests covering loadability, equal budget, CLI live
   refusal, record-replay determinism, and report-only policy invariants.
4. Run focused tests, related eval tests, `py_compile`, `git diff --check`, and
   the full suite before the supervised workflow gate.

## Risks

- Live workflows are expensive and can fail for reviewer infrastructure reasons;
  the CLI must record enough request/result context to resume or inspect without
  treating a missing verdict as success.
- Budget semantics are easy to misread because the production workflow accepts
  one `budget_usd` parameter; the bridge must record the fair total and the
  per-arm split explicitly.
- The existing runner validates P13/P14 for agentic modes, so recorded cassettes
  must preserve those probes or the replay report must fail loudly.

## Traceability

- P1: `test_agentic_eval_assembler_emits_runner_loadable_dataset`
- P2: `test_agentic_eval_assembler_enforces_equal_total_budget_and_split`
- P3: `test_agentic_eval_live_cli_refuses_without_allow_live_calls`
- P4: `test_agentic_eval_bridge_record_replay_is_deterministic` and
  `test_agentic_eval_bridge_replay_does_not_call_live_runner`
- P5: `test_agentic_eval_bridge_report_only_policy_snapshot`
