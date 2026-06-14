# TDD Grill Findings

## Finding 1: A Report Can Pass Locally But Still Fail the Supervisor Gate

The TDD plan must not count local commands as a substitute for the terminal durable workflow verdict.

Resolution: GREEN requires polling the submitted workflow to terminal.

## Finding 2: Tests Must Not Mutate the Queue

The verification commands include read-only AXI commands and direct count reads. Any command that activates an experiment or approves a proposal is forbidden.

Resolution: forbidden outcomes explicitly ban activation, approval, denial, rollback apply, and policy overlay mutation.

## Finding 3: Live-Proven Requires Before/After Counter Discipline

The audit should capture proposal/approval/rollback counts as live evidence. If counts change, the report must explain why.

Resolution: GREEN includes live ledger counts and no-mutation confirmation.

## Finding 4: Postgres Parity Cannot Be Claimed Without DSN

The audit may report Postgres parity as skipped or blocked if `CODEX_SUPERVISOR_POSTGRES_TEST_DSN` is unset, but cannot claim it passed.

Resolution: forbidden outcomes include unsupported claims; report must classify DSN-gated parity separately.

## Waivers

None.
