# PRD Grill Findings

## Scope

This grill reviewed the PRD against the Phase 0 halt evidence, repository public-boundary guidance, the existing batch-driver interface, and the forbidden outcome list. Findings are resolved before issue slicing.

### Finding 1: Atomic writes must cover every driver-final artifact

Status: resolved

Concern: The task names `curated-roster.json` and `phase0-gate-decision.json`, but the same failure mode applies to `batch-driver-manifest.json`, solver reports, and candidate corpus reports.

Resolution: P1 covers every driver-final JSON artifact written by the batch driver, and the implementation decisions route final artifacts through one atomic write layer.

### Finding 2: Resume must distrust corrupt checkpoint receipts

Status: resolved

Concern: Resume can become a silent data-integrity problem if a checkpoint file exists but is truncated, manually edited, or from a different payload.

Resolution: P3 requires hash verification before skip, and corrupt checkpoint receipts must rerun instead of being accepted.

### Finding 3: Disk-floor behavior must produce evidence before exit

Status: resolved

Concern: A disk-floor guard that exits without a halt receipt would recreate the ambiguous missing-artifact failure pattern.

Resolution: P4 requires a blocked-execution halt receipt with `reason=disk_floor_reached`, nonzero exit, and no partial final artifact promotion.

### Finding 4: Image pruning must be measurable

Status: resolved

Concern: The failed run already had pruning enabled, so a new default command without telemetry would not prove the fix.

Resolution: P5 requires image-cache size before/after and reclaimed bytes when measurable, with an explicit unavailable measurement when Docker cannot report size.

### Finding 5: Robustness must not alter benchmark authority

Status: resolved

Concern: Infrastructure hardening can accidentally change run authority, thresholds, or inclusion logic while touching the batch driver.

Resolution: P7 preserves curation predicates, thresholds, solver spend gates, and report-only authority flags, with existing tests retained as regression coverage.
