# PRD Grill Findings

## Status

Accepted after resolving three risks in the PRD text.

## Findings

1. The term "produced baseline" could overclaim live agent generation. Resolution: the PRD now says replayable fixture baseline receipts and keeps calibration-only labeling.
2. The configured reviewer panel could be treated as optional. Resolution: P1 requires the slice to fail when `supervisor_full_gate` is unavailable.
3. The report could continue using metadata accept-all as the headline. Resolution: P2 requires `supervisor_vs_single_agent_baseline` as the primary comparison while keeping metadata as non-primary.
4. Reviewer rationales could be lost in summary-only row fields. Resolution: P3 requires per-candidate rationale evidence alongside reviewer ids, packet hashes, and panel decisions.
5. Calibration evidence could leak into policy evolution. Resolution: P5 preserves every report-only authority flag as false.

## Waivers

No waivers. The fixture corpus remains intentionally small, and the PRD labels the resulting evidence as calibration rather than production proof.
