# Issues

## Slice 1: Preserve unavailable oracle semantics through replay reports

Priority: P0

Scope: Add unavailable semantics to official adapter failures and bridge interpretation so missing labels cannot be counted as oracle-bad patches. The slice covers adapter failure results, oracle output rows, bridge report metric suppression, and top-level official replay unavailable reasons.

PRD promise: P1, P3, P5

Public boundary for first RED test: `swebench_mergeability_official_replay_runner`

Chosen seam or interface: official replay runner receiving an adapter outcome mapping

Acceptance criteria:

- [ ] Nonzero official harness adapter results make the replay report unavailable rather than producing fail/fail metrics.
- [ ] Unavailable oracle rows include oracle_unavailable and oracle_unavailable_reason in persisted oracle outputs.
- [ ] FAR/TAR/FRR metrics are suppressed or unavailable with metrics_unavailable_reasons when oracle labels are unavailable.
- [ ] Report-only invariants remain false after metric suppression.

## Slice 2: Keep valid official failures and receipt validation honest

Priority: P0

Scope: Ensure unavailable handling does not swallow valid official unresolved reports and does not allow empty receipts to pass validation. The slice covers valid report parsing, adapter receipt status fields, return-code handling, artifact-path handling, and unavailable reason requirements.

PRD promise: P2, P4, P5

Public boundary for first RED test: `run_official_harness_oracle`

Chosen seam or interface: official harness adapter result and receipt validation consumed by replay reports

Acceptance criteria:

- [ ] A valid unresolved official harness report still returns fail for failed FAIL_TO_PASS tests.
- [ ] Nonzero adapter return preserves command hashes, return code, and unavailable reason in the receipt.
- [ ] Unavailable receipt validation allows nonzero return code but still requires command metadata and reason evidence.
- [ ] Existing successful official smoke semantics remain unchanged for report-only diagnostics.
