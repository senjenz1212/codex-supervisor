# Real Mergeability Reviewer Roster Diagnostic PRD

## Problem Statement

The current fixture panel proves that the reviewer machinery can run, but fixture-only saturated agreement cannot justify selecting a smaller reviewer roster. Operators need a report-only diagnostic that measures reviewer value on real or disagreement-enriched same-pool candidates, while keeping fixture-only evidence blocked from roster selection.

## Solution

Add a mergeability reviewer-roster diagnostic report that evaluates the same candidate pool across S_probe, S_probe plus individual reviewer-family arms, and S_probe plus the full panel. The report must publish FAR, TAR, FRR, per-reviewer error rates, pairwise agreement, oracle-error overlap, abstention coverage, generator/reviewer self-preference warnings, and a machine-readable roster-selection guard.

## User Stories

1. As an evaluator operator, I want every roster arm scored on the same candidate pool, so that arm differences are not sample artifacts.
2. As an evaluator operator, I want fixture-only diagnostics to remain blocked from reviewer selection, so that easy calibration evidence cannot drop reviewer diversity.
3. As an evaluator operator, I want zero-error reviewer sets to say independence is unavailable, so that saturated fixtures are not mistaken for proof of independent votes.
4. As an evaluator operator, I want disagreement without oracle-grounded reviewer errors to block roster changes, so that style disagreement alone cannot select a roster.
5. As an evaluator operator, I want self-preference warnings when generator and decisive reviewer families match, so that same-family judging risk is visible.
6. As an evaluator operator, I want abstentions reported separately from FAR/TAR acceptance, so that human-review coverage is visible without becoming an accept.
7. As a report consumer, I want all outputs to remain report-only, so that diagnostics cannot mutate policy or create applyable improvement claims.

## PRD Promise Contracts

- P1 Same-pool roster arms.
  - User-visible promise: the diagnostic reports S_probe, S_probe plus individual reviewer-family arms, and S_probe plus full panel over the identical candidate pool.
  - Public boundary: `run_mergeability_reviewer_roster_diagnostic`.
  - Chosen seam/interface: mergeability report dictionary returned by the public runner.
  - Allowed outcomes: every arm carries the same `candidate_pool_sha256`; unknown candidate receipts are rejected.
  - Forbidden outcomes: comparing roster arms over different candidate sets.

- P2 Fixture evidence cannot select rosters.
  - User-visible promise: fixture-only diagnostics keep `roster_selection_allowed=false`.
  - Public boundary: `run_mergeability_reviewer_roster_diagnostic`.
  - Chosen seam/interface: top-level `roster_selection_guard`.
  - Allowed outcomes: fixture metrics are reported as diagnostic-only.
  - Forbidden outcomes: selecting Codex-only or dropping reviewers from fixture-only evidence.

- P3 Independence is only estimated from oracle-grounded reviewer errors.
  - User-visible promise: zero-error reviewer sets report effective-vote independence as unavailable rather than perfect.
  - Public boundary: `run_mergeability_reviewer_roster_diagnostic`.
  - Chosen seam/interface: `effective_vote_estimate`.
  - Allowed outcomes: an estimate is computed only when reviewer decisions have oracle-grounded errors.
  - Forbidden outcomes: treating 100% agreement or zero errors as proof that reviewers are independent.

- P4 Disagreement needs oracle grounding before roster selection.
  - User-visible promise: reviewer disagreement alone does not authorize roster changes unless it includes oracle-grounded errors on real or disagreement-enriched candidates.
  - Public boundary: `run_mergeability_reviewer_roster_diagnostic`.
  - Chosen seam/interface: `pairwise_reviewer_agreement`, `pairwise_oracle_error_overlap`, and `roster_selection_guard`.
  - Allowed outcomes: selection remains blocked for disagreement without reviewer oracle errors.
  - Forbidden outcomes: using unscored disagreement to change the roster.

- P5 Self-preference risk remains visible.
  - User-visible promise: same-family generator/reviewer decisive votes emit warnings.
  - Public boundary: `run_mergeability_reviewer_roster_diagnostic`.
  - Chosen seam/interface: `generator_disjointness`.
  - Allowed outcomes: warnings are reported and block roster authority.
  - Forbidden outcomes: allowing a single same-family reviewer as the sole decisive judge.

## Implementation Decisions

- Add a narrow public diagnostic runner rather than modifying live reviewer execution.
- Reuse existing public review, held-out oracle scoring, reviewer normalisation, per-reviewer arms, agreement, provenance, rubric coverage, and self-preference helpers.
- Represent roster arms as additive report summaries over generated row keys: S_probe, S_probe plus Codex, S_probe plus cross-family text-only reviewer, S_probe plus full panel, and optional S_probe plus tool-backed cross-family reviewer.
- Reject reviewer receipts for candidates outside the evaluated pool.
- Keep all outputs report-only and non-applyable.

## Testing Decisions

- Start with public-boundary tests that call `run_mergeability_reviewer_roster_diagnostic`.
- Verify observable report fields, not helper internals.
- Include fixture-only, zero-error, disagreement-without-error, self-preference, and positive disagreement-enriched cases.
- Reuse existing fixture candidates and deterministic reviewer-result receipts.

## Out of Scope

- Running live SWE-bench.
- Selecting or changing the production reviewer roster.
- Mutating supervisor policy.
- Treating LLM reviewer labels as oracle labels.
- Replacing the existing paired acceptance or powered factorial reports.
