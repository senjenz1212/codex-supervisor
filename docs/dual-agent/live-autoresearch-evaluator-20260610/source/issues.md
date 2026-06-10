# Issue Slices: Live AutoResearch Evaluators

## Slice 1 - Runtime Evaluator Contract

Priority: P1

Scope: Add the executable evaluator runner and wire live execution into `run_autoresearch_fixture(...)`.

Acceptance criteria:

- [ ] A hash-pinned Python evaluator runs only when `execution_mode=live`.
- [ ] Hash mismatch prevents evaluator execution.
- [ ] The evaluator-run artifact records script hash, trial count, metrics, timeout, budget, and stdout hashes.

## Slice 2 - Validation Gaming Flags

Priority: P1

Scope: Strengthen `validate_attempt(...)` so live metrics require runtime provenance.

Acceptance criteria:

- [ ] Fixture-only metrics are rejected with `evaluator_not_executed`.
- [ ] Unknown evidence refs are rejected with `dangling_evidence_ref`.
- [ ] Identical multi-trial metrics are flagged with `zero_variance_trials` without becoming fatal by itself.

## Slice 3 - Isolated Mutable Worktree

Priority: P1

Scope: Run evaluators against an isolated attempt worktree and detect side effects outside `mutable_paths`.

Acceptance criteria:

- [ ] Evaluator writes inside mutable paths are allowed.
- [ ] Evaluator writes outside mutable paths reject the attempt.
- [ ] The source checkout does not receive evaluator side effects.

## Slice 4 - CLI Live Mode

Priority: P2

Scope: Keep default fixture replay guarded while allowing explicit live execution with `--allow-live`.

Acceptance criteria:

- [ ] `--execution-mode live` without `--allow-live` fails.
- [ ] `--execution-mode live --allow-live` writes a report and evaluator-run artifact.
- [ ] Report-only fields stay false in CLI output.
