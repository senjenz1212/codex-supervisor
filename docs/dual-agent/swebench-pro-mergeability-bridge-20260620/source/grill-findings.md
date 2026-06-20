# PRD Grill Findings

## Decision

accept

### Finding 1: Separate solve-rate from mergeability

status: resolved

The PRD originally risked implying that the existing SWE-bench adapter could simply be wired into FAR/TAR reporting. The PRD now explicitly separates the existing pass-at-k solve-rate adapter from the new mergeability bridge.

### Finding 2: Make S_probe substrate explicit

status: resolved

The public-probe substrate was the load-bearing ambiguity. The PRD now chooses static patch applicability plus curated public lint/build commands, and names that choice in P2 so tests can reject silent assumptions.

### Finding 3: Freeze decisions before oracle attachment

status: resolved

Oracle timing needed to be made explicit. P3 now requires arm decisions to be frozen before FAIL_TO_PASS/PASS_TO_PASS outcomes are attached.

### Finding 4: Treat reviewer unavailability as unavailable

status: resolved

Reviewer unavailable behavior could otherwise become an accidental accept. P4 and the testing decisions require S_full unavailable rows rather than imputation.

### Finding 5: Keep policy authority out of scope

status: resolved

Policy authority is out of scope. P6 and Out of Scope require report-only invariants and no policy mutation.

## Residual Risks

The static S_probe substrate is intentionally conservative and may be weak compared with future repo-specific public tests. The report must label this clearly so readers do not overstate deterministic floor strength. Live SWE-bench Pro access, licensing, and official harness execution remain later operational decisions.
