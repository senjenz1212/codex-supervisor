# PRD Grill Findings

### Finding 1: Draft Rows Need A Hard Operator Boundary

Status: resolved

Risk: A generated experiment could be treated as approval to spend evaluator
budget, which would violate the program rule that autonomy stops at draft.

Resolution: The PRD requires `status=draft` as the default state, an explicit
operator activation transition, and a runner that consumes only `runnable` rows.
Tests assert draft rows create no report and no workflow job.

### Finding 2: Immutable Surfaces Need Visibility Without Execution

Status: resolved

Risk: Simply skipping immutable-surface signals would hide important stability
problems, while executing them would undermine the policy boundary.

Resolution: The PRD defines `status=report_only` with a proposal pointer and no
mutable paths. The activation boundary returns report-only rows unchanged, and
the runner ignores them.

### Finding 3: The Runner Must Reuse The Existing Evaluator Lane

Status: resolved

Risk: A direct subprocess path would duplicate Slice 9 evaluator durability and
make budget or timeout enforcement inconsistent.

Resolution: The PRD requires `run_autoresearch_fixture` in live mode, using the
existing durable evaluator job lane. The acceptance test checks that a workflow
job row is recorded when a runnable experiment executes.

### Finding 4: Recurrence Clustering Needs Idempotent Provenance

Status: resolved

Risk: A generator that emits a new row on every scan would create noisy,
duplicated experiments and make operator review harder.

Resolution: The PRD requires clustering by task class, gate, and taxonomy code,
deduplication by `signal_key`, and provenance fields containing event ids, run
ids, lesson ids, and implicated paths.
