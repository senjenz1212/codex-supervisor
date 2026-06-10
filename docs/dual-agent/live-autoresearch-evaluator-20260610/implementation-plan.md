# Implementation Plan

1. Extend AutoResearch schema with runtime provenance fields:
   - `metric_source`
   - `evaluator_run_ref`
   - `evaluator_run_hash`
   - `timeout_s`

2. Add `supervisor/autoresearch/evaluator.py`:
   - verify evaluator hash before execution
   - materialize an isolated attempt worktree
   - run evaluator `k_trials` times with timeout
   - parse stdout JSON into metric values
   - write a hashable execution artifact
   - detect non-mutable side effects in the attempt worktree

3. Update orchestrator:
   - allow `execution_mode` override from the CLI
   - in live mode, emit durable execution-job events
   - replace fixture metric trials with evaluator-computed trials
   - validate the runtime-backed attempt and emit the report

4. Update validation:
   - reject `metric_trials` without evaluator execution proof
   - flag `dangling_evidence_ref`
   - flag `zero_variance_trials`
   - keep `evaluator_hash_mismatch` as blocking
   - preserve report-only invariants

5. Update tests and fixtures:
   - add executable evaluator fixtures
   - update existing accepted attempts to include evaluator execution provenance
   - add live CLI and orchestrator tests

6. Run focused tests, compile, diff check, full suite, then durable supervisor gates.
