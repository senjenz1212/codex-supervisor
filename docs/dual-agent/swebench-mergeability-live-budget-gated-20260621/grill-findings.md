# PRD Grill Findings

## Finding 1: Live Must Not Mean Default

Status: resolved.

The PRD now states that replay remains the default and live generation requires both `allow_live=true` and a positive budget before any generator adapter is invoked.

## Finding 2: Generator Inputs Could Leak Oracle Material

Status: resolved.

The PRD requires public-only generator inputs and hidden-oracle leak scanning before generators or reviewers receive payloads.

## Finding 3: Live Calibration Could Be Overclaimed

Status: resolved.

The PRD explicitly keeps live output report-only and non-applyable until a later powered-evidence slice.
