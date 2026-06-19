### Finding 1

status: resolved

The PRD could have treated the public-check arm as the full supervisor gate, which would repeat the earlier measurement mismatch. Resolution: P1 and P3 require separate `supervisor_candidate_review` and `supervisor_full_gate` arms, and the implementation decisions name `run_paired_acceptance_pilot` as the report boundary where both arms must be visible.

### Finding 2

status: resolved

Reviewer packets could leak hidden oracle material while still appearing structurally independent. Resolution: P2 explicitly bans hidden tests, final scores, expected outcomes, protected paths, and oracle labels from reviewer input, and the testing decisions require leak-detection tests below the public report boundary.

### Finding 3

status: resolved

The slice could overstate calibration output as an applyable improvement result. Resolution: P5 keeps all policy and gate authority flags false, while Out of Scope reserves corpus scale, live generation, powered claims, and policy evolution for later slices.
