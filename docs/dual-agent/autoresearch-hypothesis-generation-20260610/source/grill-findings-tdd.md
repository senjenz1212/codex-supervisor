# TDD Grill Findings

### Finding 1: Tests Must Prove The Draft Boundary

Status: resolved

Risk: A generator test that only inspects the draft payload would miss accidental
execution or hidden evaluator cost.

Resolution: `test_autoresearch_draft_cannot_run_until_operator_marks_runnable`
calls the runner before activation and asserts no report is produced and no
workflow job row exists.

### Finding 2: Durable Evaluator Reuse Must Be Observable

Status: resolved

Risk: The runner could call an evaluator script directly while still producing a
plausible report artifact.

Resolution: The runnable-path test asserts the standard AutoResearch report is
accepted and that the durable workflow job table records evaluator execution.

### Finding 3: Rejected Evaluator Reports Must Not Complete The Queue

Status: resolved

Risk: `run_autoresearch_fixture` can return a report artifact for a failed or
rejected attempt, and a runner that treats any returned report as success would
hide evaluator failures behind a completed queue row.

Resolution: `test_autoresearch_auto_runner_fails_rejected_evaluator_report`
stubs a rejected report and asserts the queue row becomes failed with an
explicit rejection reason instead of completed.

### Finding 4: Immutable Signals Need A Positive Assertion

Status: resolved

Risk: A test that only checks "no execution" would allow immutable signals to
vanish from operator view.

Resolution: `test_autoresearch_immutable_surface_signal_becomes_report_only`
asserts `status=report_only`, a proposal pointer, empty mutable paths, failed
promotion to runnable, and runner skip behavior.

### Finding 5: Weekly Cap Must Use A Stable Clock

Status: resolved

Risk: A wall-clock scheduling test can flake around week boundaries or pass for
the wrong reason when previous local state exists.

Resolution: `test_autoresearch_auto_runner_respects_weekly_cap` injects `now`,
uses a fresh state database, and verifies one completed row plus one remaining
runnable row under cap one.
