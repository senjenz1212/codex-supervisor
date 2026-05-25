# TDD Grill Findings

## Finding 1

status: resolved

Concern: Artifact tests can assert strings without proving the exported bundle
is navigable.

Resolution: Tests must read the actual exported files and assert `index.md`,
`triage.md`, and `replay/manifest.json` agree.

## Finding 2

status: resolved

Concern: Tool-call forensic tests can overfit to implementation helpers.

Resolution: Public-boundary tests inspect the `trace_envelope.tool_calls` that
the ledger would expose to operators.

## Finding 3

status: resolved

Concern: Live evidence can prove the old failure path but not the new status
split.

Resolution: Refresh the live probe and inspect `summary.json`,
`transcript.jsonl`, and `triage.md` after code validation.

## Finding 4

status: resolved

Concern: The intended receipt-block fixture does not test the timeout/no-outcome
branch that can happen during live probing.

Resolution: Add a deterministic helper test that constructs the timeout gate
result and asserts the final classification remains `P2/lead_invocation_timeout`.
