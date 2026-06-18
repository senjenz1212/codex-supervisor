# Issues

## Slice 1: Add Mergeability Bench Schema And Loader

Priority: P0

Estimate: S

PRD promise: P1.

Public boundary: importing `supervisor.mergeability_bench` and loading `tests/fixtures/mergeability_bench/tasks/*.json`.

Scope: add the dataclasses, JSON loading, schema validation, and stable serialization for mergeability tasks, candidates, and results.

Acceptance criteria:
- [ ] `load_mergeability_tasks` returns typed task records from the local fixture corpus.
- [ ] Invalid task payloads fail with a deterministic `MergeabilityBenchError`.
- [ ] Task records expose hidden, reverse, scope, and secondary rubric fields without invoking an LLM.

## Slice 2: Implement Deterministic Isolated Grading

Priority: P0

Estimate: M

PRD promises: P1, P2, P4.

Public boundary: `grade_mergeability_candidate(task, candidate)` returning `MergeabilityResult`.

Scope: implement temporary workspace grading, command execution, scope validation, reverse-classical checks, blocker aggregation, and result receipts.

Acceptance criteria:
- [ ] No-op and known-bad controls produce final score `0.0` with blocker failures.
- [ ] Known-good produces final score `1.0` when all deterministic blockers pass.
- [ ] Candidate writes outside allowed mutable paths fail before test execution.
- [ ] Candidate attempts to edit hidden tests or evaluator files are rejected.

## Slice 3: Add AutoResearch Mergeability Evaluator

Priority: P1

Estimate: S

PRD promises: P3, P4.

Public boundary: `supervisor/autoresearch/evaluators/mergeability_bench.py` executed as a hash-pinned evaluator_ref by the existing AutoResearch runner.

Scope: add the evaluator script, wire fixture controls, and prove the evaluator output remains report-only when consumed by AutoResearch.

Acceptance criteria:
- [ ] The evaluator emits JSON with `metric_value`, `metrics`, evidence refs, and runtime-native mergeability receipt metadata.
- [ ] A live AutoResearch fixture can use the evaluator_ref and produce computed trials.
- [ ] Existing AutoResearch validation still rejects saturated zero-variance attempts from becoming applyable policy proposals.
