## Slice 1 - Add the full-gate report arm

Priority: high

Scope: Extend the mergeability paired report so `run_paired_acceptance_pilot` emits both the existing public-check arm and a new full-gate arm with reviewer-panel decision data. This slice cuts through report construction, row payloads, exported artifacts, and non-applyable calibration status.

PRD promise: P1, P3, P4, P5

Public boundary: `run_paired_acceptance_pilot`

Chosen seam: the report-producing interface remains the seam; reviewer execution is injected below it for deterministic tests.

Acceptance criteria:

- [ ] The report contains `supervisor_candidate_review` and `supervisor_full_gate` arms with separate decision sources.
- [ ] A full-gate unavailable reviewer result marks the full-gate metric unavailable and does not count as acceptance.
- [ ] Disagreements among public-check, full-gate, and oracle-ceiling arms are preserved in per-candidate rows.
- [ ] Calibration authority flags remain false.

## Slice 2 - Build oracle-isolated reviewer packets for mergeability candidates

Priority: high

Scope: Construct the reviewer packet used by the full-gate arm from public task, candidate, runtime, and receipt evidence, then reject or mark unavailable any packet with hidden oracle references. This slice proves the reviewer sees enough context to judge the candidate without seeing the answer key.

PRD promise: P2, P3, P4

Public boundary: `run_paired_acceptance_pilot`

Chosen seam: reviewer packet construction is an internal seam below the report boundary and must be exercised through report rows first.

Acceptance criteria:

- [ ] Reviewer packet refs are recorded on each full-gate row.
- [ ] Hidden tests, final score, expected outcome, oracle labels, and protected path content are absent from the packet payload.
- [ ] A detected oracle leak adds a gaming flag and prevents an applyable metric claim.
- [ ] Existing public-check isolation behavior remains unchanged.

## Slice 3 - Preserve panel marginal metrics without policy authority

Priority: medium

Scope: Add derived report fields for public-check versus full-gate false-accept and true-accept rates, including panel marginal delta where matched true-accept is computable. The output remains calibration evidence, not a policy proposal.

PRD promise: P1, P4, P5

Public boundary: `run_paired_acceptance_pilot`

Chosen seam: report metric derivation behind the paired-report interface.

Acceptance criteria:

- [ ] FAR, TAR, and FRR are present for baseline, public-check, full-gate, and oracle-ceiling arms.
- [ ] Panel marginal delta is present only when denominators and matched true-accept conditions make it meaningful.
- [ ] The report cannot create an applyable policy proposal from calibration-only evidence.
- [ ] Existing mergeability evaluator and AutoResearch report-only tests remain green.
