# Issues

## Slice ISS-1: Configured Reviewer Panel Adapter

Type: AFK
Priority: P0
Estimate: M
Scope: Add a mergeability reviewer-panel adapter that invokes the configured reviewer roster through the existing reviewer registry seam and returns a normalized panel result for the full-gate arm.
PRD promise: P1, P3, P4
First public-boundary RED test: `test_run_paired_acceptance_pilot_uses_configured_panel_when_requested`

Acceptance Criteria:
- [ ] Calibration can request configured-panel mode without passing a custom panel callable.
- [ ] Fake configured reviewers produce recorded independent reviewer results and a conservative panel decision.
- [ ] Missing reviewer verdicts make S_full unavailable or rejected rather than accepted.
- [ ] The existing S_probe arm and legacy supervisor alias remain unchanged.

## Slice ISS-2: Oracle Isolation and Report-Only Guardrails

Type: AFK
Priority: P0
Estimate: M
Scope: Reuse the existing oracle leak detector before reviewer invocation and preserve non-applyable calibration reporting after panel wiring.
PRD promise: P2, P3, P4
First public-boundary RED test: `test_configured_panel_not_invoked_when_reviewer_packet_contains_oracle_material`

Acceptance Criteria:
- [ ] Hidden tests, final scores, expected outcomes, oracle labels, and protected path content are absent from reviewer requests and packets.
- [ ] A detected leak prevents reviewer invocation and records an oracle isolation violation.
- [ ] The report records reviewer packet refs, reviewer result summaries, panel decisions, and S_probe versus S_full disagreement.
- [ ] Calibration reports cannot create applyable policy proposals or mutate gate policy.

## Slice ISS-3: Replayable Reviewer Evidence

Type: AFK
Priority: P1
Estimate: S
Scope: Add stable evidence fields for configured reviewer panel decisions so audits can reconstruct which reviewer verdicts affected S_full.
PRD promise: P4
First public-boundary RED test: `test_configured_panel_report_records_reviewer_results_and_packet_refs`

Acceptance Criteria:
- [ ] Report rows include reviewer packet references and independent reviewer result summaries.
- [ ] The arm summary exposes availability status and unavailable counts.
- [ ] S_full disagreement with S_probe is preserved rather than collapsed into the public-check arm.
