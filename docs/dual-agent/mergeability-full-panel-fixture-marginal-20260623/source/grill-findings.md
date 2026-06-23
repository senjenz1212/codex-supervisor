# PRD Grill Findings

Status: accepted after incorporating the hard gates below.

- Resolved: The PRD limits the claim to fixture calibration and explicitly rejects production improvement, live benchmark, and human mergeability interpretations.
- Resolved: Full-panel evidence requires both configured reviewers; missing Cursor or Codex verdicts make S_full unavailable rather than accepted or imputed.
- Resolved: Per-reviewer arms are required as a separate report surface so the conservative S_full aggregate does not hide individual reviewer behavior.
- Resolved: The marginal metric must be computed only at matched true-accept, with not_matched or unavailable status treated as honest evidence.
- Resolved: Oracle-isolation proof is part of the acceptance contract through reviewer packet leak scans and report-only invariants.
