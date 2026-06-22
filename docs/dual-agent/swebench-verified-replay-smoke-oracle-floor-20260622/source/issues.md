## Slice 1: Run Official Replay Smoke With Selected Rows

Scope: Run the public official SWE-bench replay smoke path for a tiny selected set, using existing filtered replay seams and oracle-runner receipts.

Priority: high

Acceptance criteria:
- [ ] A tiny selected SWE-bench Verified replay writes official_replay_report.json and official_replay_manifest.json.
- [ ] The report contains nonzero selected instance and candidate counts and does not require predictions for non-selected rows.
- [ ] Frozen decisions are persisted before oracle receipts are produced.

PRD promises: P1, P2, P3.
Public boundary: official SWE-bench replay CLI or replay runner invocation.

## Slice 2: Preserve Smoke-Only Reporting Semantics

Scope: Keep the official replay smoke report-only, label SWE-bench Verified as plumbing smoke, and suppress full-panel claims without valid roster evidence.

Priority: high

Acceptance criteria:
- [ ] Verified reports are labeled plumbing_smoke_only with contamination and test-pass-oracle caveats.
- [ ] Full-panel metrics are unavailable unless full roster availability is valid.
- [ ] No policy proposal, default mutation, or improvement claim is emitted from the smoke.

PRD promises: P4, P5, P6.
Public boundary: emitted official replay report and bridge FAR/TAR rows.
