## Problem Statement

Current mergeability measurement can make reviewer infrastructure outages look like panel quality failures. That is dangerous because production acceptance must still block on missing reviewer verdicts, while evaluation reports need to distinguish unavailable infrastructure from a real reviewer decision that rejected a candidate. Without that separation, panel marginal metrics can understate true accept behavior, overstate false reject behavior, and produce confusing calibration rows for the real SWE-bench benchmark path.

## Solution

Add explicit measurement semantics for full reviewer roster availability, missing verdict blocks, infrastructure unavailable states, panel quality rejections, and Codex-only calibration mode. Preserve the conservative production gate: missing or malformed reviewer verdicts still block acceptance. In reports, compute FAR/TAR panel deltas only for rows where the expected full roster was actually available, and record diagnostics that explain reviewer-0 failures with recoverability and transcript or receipt references.

## User Stories

1. As a benchmark operator, I want missing reviewer verdicts labeled separately from panel rejections, so that calibration reports do not confuse outage with quality.
2. As a supervisor maintainer, I want production gates to remain conservative, so that unavailable reviewers never silently become acceptance.
3. As an evaluator, I want panel marginal metrics to refuse zero-availability comparisons, so that S_full numbers are not built from empty reviewer evidence.
4. As a reviewer infrastructure owner, I want reviewer-0 failure diagnostics, so that runtime, auth, model, invocation, and transcript issues can be repaired.
5. As a researcher, I want Codex-only smoke calibration labeled explicitly, so that partial-roster diagnostics cannot travel as full-panel evidence.

## PRD Promise Contracts

P1. Missing reviewer verdicts continue to block production/full-gate acceptance and are reported as panel_missing_verdict_block rather than panel_quality_reject.
P2. Fully available reviewer rosters that reject a candidate are reported as panel_quality_reject, with full_roster_available evidence attached to the row.
P3. FAR/TAR panel marginal reporting computes only on fully available roster rows, or returns unavailable with an insufficient availability reason.
P4. Reviewer infrastructure failures record a diagnostic reason, recoverability, and transcript or receipt hash evidence without exposing hidden oracle material.
P5. Codex-only calibration mode is explicitly labeled codex_only_calibration and cannot be reported as full-panel evidence or used for policy proposal creation.

## Implementation Decisions

The measurement interface remains the mergeability report boundary because that is where S_probe, S_full, reviewer packets, and aggregate FAR/TAR outputs meet. The production gate decision source remains unchanged and fail-closed. The new logic should deepen existing reviewer-panel normalization and report summarization instead of adding a parallel benchmark format. Existing configured reviewer-panel adapters should feed diagnostics into the same public report surface.

## Testing Decisions

Tests start at mergeability report and configured panel public boundaries, not private helper-only seams. The first red tests should prove missing reviewer verdicts block production while producing the new measurement labels. Follow-on tests should cover available rejecting panels, zero-availability panel marginal refusal, Codex-only calibration labeling, reviewer-0 infrastructure diagnostics, and report-only invariants.

## Out of Scope

This slice does not weaken production gate authority, does not make unavailable reviewers acceptable, does not run a powered benchmark, does not create policy proposals, and does not claim human maintainer mergeability. Live SWE-bench generation, official oracle execution changes, and powered FAR/TAR promotion criteria belong to later slices.
