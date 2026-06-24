# Mergeability Roster Diagnostic Guardrails PRD

## Problem Statement

The mergeability fixture corpus can prove reviewer plumbing, but it is saturated for reviewer-roster selection: the latest full-panel fixture run reported complete reviewer agreement and perfect individual reviewer FAR/TAR on reviewed rows. If the system lets that fixture-only evidence select a smaller roster, it can incorrectly drop reviewer diversity before real or disagreement-enriched candidates expose self-preference, family-correlation, or hard-case disagreement.

## Solution

Make fixture roster diagnostics explicitly diagnostic-only. The paired acceptance report must preserve existing S_probe, S_full, per-reviewer, and Codex-only calibration metrics, but it must also publish a machine-readable roster-selection guard that blocks fixture-only roster selection and records what evidence is still required before a roster can be promoted.

## User Stories

1. As an evaluator operator, I want fixture ablations to run without implying roster authority, so that plumbing evidence cannot quietly become a policy decision.
2. As an evaluator operator, I want Codex-only calibration labeled separately from full-panel evidence, so that a single-reviewer diagnostic cannot be mistaken for supervisor improvement.
3. As an evaluator operator, I want saturated reviewer agreement called out, so that 100% agreement on easy fixtures is not used to argue that diversity has no value.
4. As an evaluator operator, I want changed rosters to invalidate checkpoint reuse, so that a new reviewer configuration recomputes reviewer work.
5. As an evaluator operator, I want real/disagreement evidence requirements recorded, so that the first real benchmark compares rosters on the same candidate pool before any selection.
6. As a reviewer of reports, I want the public dashboard to carry the same diagnostic-only warning, so that annotation surfaces do not overstate fixture results.

## PRD Promise Contracts

- P1 Fixture-only reports block roster selection.
  - User-visible promise: any fixture roster diagnostic report says roster selection is not allowed.
  - Public boundary: `mergeability_fixture_measurement`.
  - Selected seam/interface: `run_fixture_panel_produced_baseline_measurement` and `run_bounded_parallel_panel_corpus` report dictionaries.
  - Allowed outcomes: diagnostic metrics, unavailable reasons, and next-evidence requirements are reported.
  - Forbidden outcomes: selecting Codex-only, dropping reviewers, or creating an applyable policy proposal from fixture-only evidence.
  - Report-only invariant: `metric_applyable=false`, `improvement_claim_allowed=false`, `policy_mutated=false`, `gate_advanced=false`.

- P2 Codex-only calibration remains calibration-only.
  - User-visible promise: Codex-only results may be compared as a diagnostic arm but never as full-panel evidence.
  - Public boundary: `mergeability_fixture_measurement`.
  - Selected seam/interface: `codex_only_calibration_panel` and `configured_reviewer_panel.report_mode`.
  - Allowed outcomes: `report_mode=codex_only_calibration`, `full_panel_evidence_allowed=false`, calibration marginal reported when computable.
  - Forbidden outcomes: Codex-only calibration setting `full_panel_evidence_allowed=true` or authorizing roster selection.
  - Report-only invariant: no policy proposal is derivable.

- P3 Saturated fixture agreement is marked as non-selection evidence.
  - User-visible promise: if reviewer agreement is saturated, the report records that it cannot prove diversity is unnecessary.
  - Public boundary: `mergeability_fixture_measurement`.
  - Selected seam/interface: configured reviewer panel report block.
  - Allowed outcomes: agreement metrics are reported alongside a reason such as `fixture_corpus_saturated_for_roster_selection`.
  - Forbidden outcomes: 100% fixture agreement justifies dropping a reviewer.
  - Report-only invariant: roster selection remains blocked.

- P4 Roster/cache provenance is explicit.
  - User-visible promise: changed roster evidence recomputes or records why cache reuse is valid.
  - Public boundary: `mergeability_fixture_measurement`.
  - Selected seam/interface: bounded runner identity, option hash, and reviewer roster ids.
  - Allowed outcomes: cache policy states roster and option changes invalidate checkpoints.
  - Forbidden outcomes: changed reviewer roster silently reuses stale checkpoint evidence.
  - Report-only invariant: cache provenance does not authorize promotion.

- P5 Real benchmark roster selection requires same-pool hard/disagreement evidence.
  - User-visible promise: the report names the minimum evidence required before choosing a reviewer roster.
  - Public boundary: `mergeability_fixture_measurement`.
  - Selected seam/interface: top-level `roster_selection_guard`.
  - Allowed outcomes: required evidence points to real SWE-bench/replay/live or disagreement-enriched candidate pools.
  - Forbidden outcomes: fixture-only metric deltas select a production roster.
  - Report-only invariant: promotion stays blocked until powered, oracle-grounded evidence exists.

## Implementation Decisions

- Reuse the existing mergeability fixture measurement seam instead of adding a parallel evaluator.
- Add a small report-block builder that consumes existing report summaries, reviewer agreement, and diagnostic scope.
- Put the guard in both the top-level report and the configured-panel block for machine consumers.
- Preserve existing report schema fields and add only additive metadata.
- Keep cache enforcement in the existing checkpoint identity; add explicit report text that roster ids and option hash control reuse.

## Testing Decisions

- Start with public-boundary tests that call `run_fixture_panel_produced_baseline_measurement` or `run_bounded_parallel_panel_corpus`.
- Test observable report fields, not helper internals.
- Include a saturated-agreement fixture case and a Codex-only calibration case.
- Reuse the existing stale-checkpoint test as behavioral evidence that changed identity recomputes; add report-level cache-policy assertions.

## Out of Scope

- Live SWE-bench candidate generation.
- Selecting the production reviewer roster.
- Changing reviewer-panel aggregation semantics.
- Adding a new external issue tracker.
- Treating LLM reviewer labels as oracle labels.
