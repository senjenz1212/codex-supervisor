# PRD Grill Findings

## Findings

### Finding G1

Status: resolved
Severity: high
Question: Could the concurrency work become a private scheduler exercise instead of an operator-facing runner?
Resolution: The PRD and TDD plan choose the public fixture measurement runner as the seam, with worker scheduling, ordering, reuse, and leak checks hidden behind that interface.

### Finding G2

Status: resolved
Severity: high
Question: Could success be misreported if panel marginal is unavailable or unfavorable?
Resolution: Success criteria require honest panel marginal status, S_probe-versus-S_full discordant counts, per-reviewer arms, and inter-reviewer agreement rather than a favorable computed number.

### Finding G3

Status: resolved
Severity: high
Question: Could checkpoint reuse introduce stale evidence or leak hidden oracle data?
Resolution: Checkpoint identity must include candidate hash, reviewer packet hash, reviewer roster identity, option metadata, and schema version, and stale or malformed checkpoints must be recomputed.

### Finding G4

Status: resolved
Severity: medium
Question: Could Lavish become part of benchmark scoring by accident?
Resolution: Lavish is explicitly review UX only. The HTML artifact is public-only and excluded from metric computation, policy proposals, and gate mutation.

### Finding G5

Status: resolved
Severity: medium
Question: Could parallel reviewer execution create resource pressure?
Resolution: The PRD requires explicit conservative defaults and worker-bound tests, with runtime evidence reporting the worker limits used for the fixture corpus run.

## Decision

No open findings remain.
