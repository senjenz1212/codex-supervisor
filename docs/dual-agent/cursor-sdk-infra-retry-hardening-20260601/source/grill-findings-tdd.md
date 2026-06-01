# TDD Grill Findings

### Finding 1

status: resolved

Concern: Unit-only tests could miss workflow wiring, leaving the retry policy
unavailable in real supervised runs.

Resolution: The TDD plan includes both `invoke_cursor_agent` boundary tests and
`run_dual_agent_workflow` wiring tests.

### Finding 2

status: resolved

Concern: A timeout retry test could become slow or flaky.

Resolution: Timeout classification remains covered by the existing watchdog
test. New retry tests use deterministic fake exceptions and injected backoff
sleep capture.

### Finding 3

status: resolved

Concern: Retrying infrastructure failures might accidentally consume the
contract retry budget.

Resolution: The plan includes a specific test proving malformed output follows
the contract retry path and does not consume infrastructure retry attempts.
