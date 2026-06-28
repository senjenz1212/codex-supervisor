# TDD Grill Findings

Task id: panel-verified-crossfamily-robust-20260626

## Finding 1: The first RED must not test a helper-only parser

Status: resolved

Resolution: the first RED crosses the actual configured reviewer result translation and cross-family status boundary.

## Finding 2: Robust aggregation test must be mutation-resistant

Status: resolved

Resolution: the robust test uses two accepting reviewers and one contaminated deny, then asserts the geometric-median diagnostics and decision. Merely asserting `aggregation_mode` would be insufficient.

## Finding 3: All-arms gate test must prove blocking, not just metadata presence

Status: resolved

Resolution: the all-arms RED asserts `status="unavailable"`, `all_arms_populated=False`, unavailable independence metrics, and `reviewer_panel_not_robustly_aggregated`.

## Finding 4: Live reviewer infra remains below the public boundary

Status: resolved

Resolution: tests fake live model calls below `ReviewerAdapter.review` and keep the result translation, aggregation, and report gates real.
