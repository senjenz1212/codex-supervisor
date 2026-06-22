## Findings

### Finding 1

Status: resolved

Concern: The PRD must keep production and measurement semantics separate.

Resolution: P1 now says missing reviewer verdicts still block production acceptance while measurement rows receive a distinct panel_missing_verdict_block label.

### Finding 2

Status: resolved

Concern: Panel marginal metrics need a denominator guard.

Resolution: P3 requires full-roster row availability before FAR/TAR deltas can be computed.

### Finding 3

Status: resolved

Concern: Codex-only smoke mode could be misread as full-panel evidence.

Resolution: P5 requires explicit codex_only_calibration labeling and blocks policy proposal use.

### Finding 4

Status: resolved

Concern: Reviewer diagnostics must not leak oracle material.

Resolution: P4 limits diagnostics to infrastructure reason, recoverability, and transcript or receipt hash evidence.
