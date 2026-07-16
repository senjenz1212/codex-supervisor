# TDD Grill Findings: LEDGER-001

1. Tests must alter persisted bytes/rows, not only call a helper with synthetic
   invalid input.
2. Prefix validity and tail completeness are separate assertions.
3. PostgreSQL tests may be environment-gated, but migration SQL and trigger
   parity remain statically testable.
4. A local HMAC and local pin directory may exercise the API but must be
   labelled hermetic.
5. Terminal and interval checkpoint tests must pass through the operational
   lifecycle boundary rather than call `checkpoint_event_ledger` directly.
