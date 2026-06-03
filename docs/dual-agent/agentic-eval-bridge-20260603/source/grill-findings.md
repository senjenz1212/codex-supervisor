# PRD Grill Findings: Agentic Eval Bridge

### Finding 1: The Bridge Must Not Recreate Synthetic Arms

status: resolved

The current two-task dataset is intentionally synthetic. The PRD now requires
`assemble_agentic_eval_task` to call a supplied workflow runner for every arm
and to preserve cassette refs from those calls, so hand-authored arm data cannot
be mistaken for measured fan-out evidence.

### Finding 2: Equal Budget Means Total Budget, Not Per Actor Budget

status: resolved

Fan-out can look better if workers receive budget in addition to the lead. The
PRD now defines a budget split artifact for agentic modes and requires equal
arm-level totals while carving lead/worker shares from that total.

### Finding 3: Live Execution Must Be Operator-Explicit

status: resolved

Tests and replay must not call live agents. The PRD now makes
`--allow-live-calls` mandatory for the live CLI and keeps the default path on
fixture replay.
