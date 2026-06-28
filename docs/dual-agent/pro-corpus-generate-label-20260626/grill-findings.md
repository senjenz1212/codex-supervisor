# PRD Grill Findings

Task id: `pro-corpus-generate-label-20260626`

## Finding 1 - Live solver dependency is still blocked

Status: resolved by PRD framing.

Concern: The Slice-2 dependency artifact reports `live_k_run_status="blocked"` because no approved live SWE-bench Pro single-agent runner command/model configuration was supplied. A PRD that simply says "run k>1" would overclaim the current state.

Resolution: P1 explicitly allows a blocked artifact and forbids treating fixture-only smoke output as real benchmark evidence.

## Finding 2 - Gold-good backstops cannot satisfy FAR

Status: resolved by P1 and P2.

Concern: The gold proof artifact gives an oracle-good candidate, but repeating gold rows alone keeps `n_bad == 0`.

Resolution: P1 requires generated oracle-bad candidates, and P2 forbids calling a gold-only corpus FAR-capable.

## Finding 3 - Non-applying patches are not negative examples

Status: resolved by P3.

Concern: A failed patch apply could be misread as oracle-bad because it is not an oracle-good pass.

Resolution: P3 requires `patch_applied=true` before an oracle-bad label can be emitted.

## Finding 4 - Report-only corpus must not become auto-evolve authority

Status: resolved by authority flags.

Concern: A real corpus could be mistaken for policy evidence.

Resolution: The PRD keeps all authority flags false and keeps the autonomous bridge out of scope.

## Decision

Accept PRD after revisions. The current slice is valid only if it either produces real generated attempts labeled by the Pro oracle or records the exact blocker.
