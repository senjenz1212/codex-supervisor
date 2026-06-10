# PRD Grill Findings

### Finding 1 - Live execution must remain advisory

Status: resolved

The PRD could be misread as enabling AutoResearch to apply a winning attempt. The promise contracts now state that live evaluator output is report-only and cannot advance gates, mutate policy, or change defaults.

### Finding 2 - Hash checks must happen before execution

Status: resolved

Checking `evaluator_hash` after subprocess execution would still allow an attacker to run swapped code. The implementation decision requires pre-execution hash verification and the tests assert that a mismatched evaluator does not create its execution marker.

### Finding 3 - Fixture metrics are the highest-risk false positive

Status: resolved

The original fixture path accepted `metric_trials` as data. The PRD and tests now require `metric_source=evaluator_execution`, an evaluator-run ref, and an evaluator-run hash before metrics can validate.

### Finding 4 - Mutable path isolation needs observable evidence

Status: resolved

The isolated worktree behavior now has a replayable evaluator-run artifact and a mutable-path escape test. The source checkout must remain unchanged when an evaluator attempts to write outside scope.
