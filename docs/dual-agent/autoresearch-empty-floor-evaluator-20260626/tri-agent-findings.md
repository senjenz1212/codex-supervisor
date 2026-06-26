# Tri-Agent Findings

Task id: `autoresearch-empty-floor-evaluator-20260626`

Exactly three read-only validation subagents were spawned.

## Agent A: Floor Is Real, No Seed Leakage

Decision: revise

Strongest finding: the plan covered the main measured-floor fix, but missed evaluator progress resume. `run_evaluator_trials` currently persists only `trial_records` and `cost_usd`; if a measured floor is stored only in the terminal durable payload, a resumed evaluator run can complete candidate trials without carrying the measured empty floor.

File refs:

- `supervisor/autoresearch/evaluator.py:808`: progress load path.
- `supervisor/autoresearch/evaluator.py:852`: progress write path.
- `supervisor/autoresearch/generator.py:499`: generated pending attempt seed.
- `supervisor/autoresearch/evaluator.py:746`: quality-control attempt JSON uses attempt baseline.
- `supervisor/autoresearch/evaluator.py:779`: control baseline fallback.

Folded change: TDD Cycle 1 now requires evaluator progress to persist and reload empty-floor evidence before durable terminal propagation.

## Agent B: Propagation Correctness

Decision: revise

Strongest finding: current `EvaluatorExecutionResult` lacks `metric_before`, `metric_after`, and `metric_delta`, so durable payloads and `run_autoresearch_fixture` have nothing to propagate. Existing live tests assert trials and run refs, but not before/after/delta or `empty_floor_comparison()`.

File refs:

- `supervisor/autoresearch/evaluator.py:19`: execution result shape lacks metric fields.
- `supervisor/autoresearch/evaluator.py:216`: evaluator run artifact lacks metric fields.
- `supervisor/autoresearch/durable_jobs.py:278`: durable execution serialization lacks metric fields.
- `supervisor/autoresearch/durable_jobs.py:331`: durable execution deserialization lacks metric fields.
- `supervisor/autoresearch/orchestrator.py:133`: orchestrator replacement omits metric fields.
- `tests/test_autoresearch.py:252`: existing durable test does not assert metric propagation.
- `tests/test_autoresearch.py:503`: existing live evaluator test does not assert empty-floor comparison.

Folded change: TDD Cycle 2 explicitly covers evaluator result fields, durable terminal payload fields, orchestrator replacement, validation, report fields, and `empty_floor_comparison()`.

## Agent C: Operator Gating Intact

Decision: accept

Strongest finding: the planned P3 routes live empty-floor evidence into the existing proposal deriver only. The deriver is draft-only and authority invariants remain false; mutation still lives behind `approve_policy_proposal`.

File refs:

- `supervisor/autoresearch/policy_evolution.py:76`: report-derived proposal public boundary.
- `supervisor/autoresearch/policy_evolution.py:140`: derived proposals are draft.
- `supervisor/autoresearch/policy_evolution.py:157`: created proposal event is emitted without apply.
- `supervisor/autoresearch/policy_evolution.py:189`: separate approval/apply path.
- `supervisor/autoresearch/policy_evolution.py:827`: authority invariants.

Folded change: no code-scope expansion; P3 remains draft-only, with direct assertions for approval-required and authority-false fields.

## Gate Result

Tri-agent validation is accepted after folding A/B revisions into `tdd.md`, `translation-audit.md`, and implementation requirements.
