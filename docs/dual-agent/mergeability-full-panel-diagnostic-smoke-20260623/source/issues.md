# Issues

## Slice 1: Run Configured Full-Panel Diagnostic Smoke

PRD promise: P1, P2, P3, P4, P5.

Priority: high

Estimate: small

Scope: Invoke the existing mergeability fixture corpus through run_paired_acceptance_pilot with reviewer_panel_mode configured, persist paired_acceptance_report.json, and record whether configured reviewer evidence makes S_full available.

Public boundary: the mergeability diagnostic invocation that writes paired_acceptance_report.json for the existing fixture corpus.

Chosen seam: run_paired_acceptance_pilot with reviewer_panel_mode configured, plus the existing configured reviewer panel adapter and report serializer.

Acceptance criteria:
- [ ] The run writes paired_acceptance_report.json under the task artifact directory.
- [ ] The report records configured reviewer panel mode and keeps S_probe separate from S_full.
- [ ] Cursor SDK reviewer evidence includes isolated-worktree diagnostics or a classified infrastructure failure.
- [ ] Codex reviewer evidence is present when S_full is available.
- [ ] S_full unavailable is never imputed as accept.
- [ ] Panel marginal status is computed or carries a concrete unavailable, not_matched, or insufficient reason.
- [ ] Report-only invariant flags remain false.

## Slice 2: Preserve Oracle Isolation And Non-Policy Semantics

PRD promise: P3, P5.

Priority: high

Estimate: small

Scope: Validate reviewer packets and report outputs so hidden oracle material stays outside reviewer prompts and no calibration diagnostic can create an applyable policy proposal.

Public boundary: report and reviewer packet validation for the full-panel diagnostic.

Chosen seam: the reviewer packet leak detector and report-only proposal derivation guard.

Acceptance criteria:
- [ ] Hidden oracle keys, protected paths, final score, oracle accept, and hidden command material are excluded from reviewer packets.
- [ ] Any oracle leak blocks reviewer panel use and records an isolation failure.
- [ ] No policy proposal can be derived from the diagnostic report.
- [ ] The diagnostic does not add rubric labels or treat hidden oracle results as reviewer blockers.
