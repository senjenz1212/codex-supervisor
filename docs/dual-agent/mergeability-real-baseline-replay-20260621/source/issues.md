# Issues

## Slice ISS-1: Produced Baseline Decision Artifact

Type: AFK
Priority: P0
Estimate: M
Scope: Add a replayable baseline decision artifact shape and require powered mergeability evaluation to consume it for the `single_agent_baseline` arm.
PRD promise: P1, P3
First public-boundary RED test: `test_powered_factorial_requires_explicit_baseline_decisions`

Acceptance Criteria:
- [ ] Powered factorial evaluation no longer uses `_baseline_accepts(candidate)` when baseline decisions are absent.
- [ ] Explicit baseline rows can mark candidates accepted, rejected, or unavailable.
- [ ] Report rows include baseline producer metadata, decision source, and candidate artifact hash.
- [ ] The candidate pool hash remains shared across compared arms.

## Slice ISS-2: Baseline Evidence Fail-Closed Validation

Type: AFK
Priority: P0
Estimate: M
Scope: Validate baseline rows against candidate ids and candidate artifact hashes, then fail closed when evidence is missing or inconsistent.
PRD promise: P2, P3
First public-boundary RED test: `test_powered_factorial_baseline_hash_mismatch_is_unavailable`

Acceptance Criteria:
- [ ] Missing baseline row for a candidate marks that baseline decision unavailable.
- [ ] Candidate hash mismatch marks that baseline decision unavailable.
- [ ] Malformed baseline rows add a baseline evidence gaming flag.
- [ ] Unavailable baseline rows do not count as accepted baseline outcomes.
- [ ] Unavailable baseline rows are excluded from rejected and false-reject counts so they do not distort TAR/FRR accounting.

## Slice ISS-3: Legacy Calibration Label Separation

Type: AFK
Priority: P1
Estimate: S
Scope: Preserve the old metadata baseline only in fixture calibration reports and label it so it cannot travel as a real produced baseline claim.
PRD promise: P4
First public-boundary RED test: `test_legacy_metadata_baseline_is_labeled_not_real_baseline`

Acceptance Criteria:
- [ ] Paired fixture calibration continues to run without produced baseline artifacts.
- [ ] Reports label metadata baseline evidence as legacy or calibration-only.
- [ ] Policy proposal derivation still ignores calibration-only baseline reports.
- [ ] Powered reports remain report-only unless powered live evidence exists.
