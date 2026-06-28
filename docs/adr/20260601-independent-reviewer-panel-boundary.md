# ADR: Independent Reviewer Panel Boundary

Date: 2026-06-01

## Status

Accepted

## Context

The supervisor historically exposed the independent reviewer through a single
Cursor-named slot: `cursor_review` plus the `tri_agent_cursor_review` event.
That slot can now be served by Cursor SDK or a lower-assurance structured
LiteLLM/Gemini fallback. The naming is therefore legacy: useful for replay
compatibility, but too narrow for the next phase where the supervisor needs a
panel of reviewers with explicit provenance.

The system is not ready to change verdict aggregation in this slice. Current
single-reviewer gate behavior must remain stable while the schema becomes
panel-shaped.

## Decision

- Add `independent_reviewer_results[]` as the panel-shaped reviewer result
  collection.
- Represent today's single reviewer as element 0 in that collection.
- Emit a new `independent_reviewer_review` event for new consumers.
- Continue writing and reading legacy `tri_agent_cursor_review`, `cursor_review`,
  and `independent_reviewer` payloads.
- Track per-reviewer runtime, model, provider family/lineage, tool access,
  assurance grade, transcript refs, output/transcript hashes, decision,
  severity, and confidence.
- Keep verdict aggregation unchanged. Real reviewer revise/deny still blocks,
  missing verdicts still do not count as accept, and calibrated weighting remains
  a later slice.

## Consequences

New reviewer-panel consumers can adopt `independent_reviewer_review` without an
atomic migration. Existing audit artifacts, replay fixtures, and transcript
readers continue to work against `tri_agent_cursor_review`.

The schema now records enough provenance for future reviewer-panel aggregation
and dependence analysis, but the values are not yet used to change gate
decisions.

## Addendum 2026-06-26: Robust Aggregation And Verified Provider Family

Subsequent slices opt the configured reviewer panel into a robust aggregation
mode without weakening any hard block:

- `evaluate_reviewer_panel(..., aggregation_mode="geometric_median")` plus
  `ConfiguredReviewerPanelOptions.panel_aggregation_mode` (also exposed as
  `--panel-aggregation-mode` on `codex-supervisor-swebench-mergeability-replay`
  and the bounded panel runner) selects a geometric-median accept rule.
  `"conservative"` remains the default for in-process callers and preserves the
  prior all-reviewers-accept ordering; the SWE-bench replay CLIs default to
  `"geometric_median"` so cross-family panels publish robust readiness.
- Robust mode never overrides a critical/important revise or deny: blocking
  reviewer objections still short-circuit to `revise`, missing verdicts still
  surface as `revise`, and accept-with-low-confidence still escalates. Only the
  remaining accept-vs-revise tie is settled by the median. When robust mode is
  requested it also takes precedence over calibrated weighting; the panel
  decision records `aggregation_mode="geometric_median"` and a
  `robust_aggregation` summary instead of `calibrated_accept`.
- Reviewer results and provenance reports now carry `provider_family_verified`
  and `provider_family_source` alongside `provider_family`. The verifier
  (`provider_family_verification_for_reviewer`) prefers the served-model family
  (`served_model`/`response_model`); operator config can name a family but is
  recorded as `operator_config` and not counted as verified.
- The official all-arms diagnostic verifier blocks cross-family readiness when
  any reviewer is `unverified_provider_family` /
  `operator_asserted_provider_family_unverified` or when any official-replay
  row reports `panel_aggregation_mode != "geometric_median"`
  (`reviewer_panel_not_robustly_aggregated`). All authority flags stay false;
  the readiness signal is report-only.
