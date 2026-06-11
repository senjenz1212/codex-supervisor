# PRD: AutoResearch Policy Diff Derivation

## Problem Statement

Accepted AutoResearch reports can currently inform policy evolution only when a caller authors `candidate_changes` separately. That keeps the loop too manual and weakens replayability: the accepted evaluator evidence, metric delta, changed artifact, and proposal diff are not derived as one auditable chain. Phase C needs the supervisor to derive the reviewable overlay proposal itself while preserving the program-wide invariant that no AutoResearch result self-applies.

## Solution

Add `derive_policy_evolution_proposals_from_report` as the public Phase C boundary and wire the real report/tool path to it. AutoResearch validation reports carry the policy candidate ref and metric before/after/delta values produced by the attempt, so the MCP operator tool can draft proposals from `report_path` without caller-authored `candidate_changes`. The deriver reads accepted AutoResearch report records, requires a non-gaming positive metric delta, derives exactly one candidate policy overlay artifact from report evidence, and emits an operator-reviewable proposal targeting only `.supervisor/policy-overlay.yaml`. The proposal is always `status=draft`, records derivation provenance, and keeps approval, gate authority, reviewer authority, and typed outcome authority unchanged.

## User Stories

- As an operator, I want an accepted AutoResearch report to produce a concrete overlay proposal so I review a diff instead of inventing one by hand.
- As a reviewer, I want the proposal to include metric before/after/delta and evaluator evidence so I can verify the recommendation is tied to a real result.
- As a maintainer, I want non-overlay or immutable-surface attempts rejected before proposal creation so policy evolution cannot escape the live overlay surface.
- As a replay investigator, I want the report ref, report hash, attempt id, affected gates, and candidate artifact ref recorded so the proposal can be reconstructed later.

## PRD Promise Contracts

P1. Accepted positive report drafts one overlay proposal.

- User-visible promise: An accepted, evaluator-backed AutoResearch record with a positive metric delta can produce exactly one draft policy proposal.
- Public boundary: `derive_policy_evolution_proposals_from_report`.
- Allowed outcomes: a draft proposal with overlay diff, evaluator evidence, metric before/after/delta, affected gates, and provenance.
- Forbidden outcomes: no proposal from rejected, gaming-flagged, non-evaluator-backed, zero-delta, or negative-delta records.

P2. Overlay surface only.

- User-visible promise: The deriver cannot produce proposals against prompts, configs, immutable paths, or arbitrary report-declared targets.
- Public boundary: `derive_policy_evolution_proposals_from_report`.
- Allowed outcomes: only `.supervisor/policy-overlay.yaml` target changes are draftable.
- Forbidden outcomes: deriving or applying a proposal for any non-overlay target.

P3. Human approval still required.

- User-visible promise: Derived proposals are reviewable drafts; they cannot mutate policy or advance gates.
- Public boundary: proposal payload returned by `derive_policy_evolution_proposals_from_report`.
- Allowed outcomes: `status=draft`, `requires_operator_approval=true`, `default_change_allowed=false`, `automatic_policy_mutation=false`, `gate_advanced=false`.
- Forbidden outcomes: applying the diff, marking a proposal approved, or changing gate/reviewer/typed-outcome authority.

P4. Replayable provenance.

- User-visible promise: Every derived proposal can be traced back to the accepted report, attempt, evaluator run, and metric delta that justified it.
- Public boundary: proposal `derivation` and `evaluator_evidence` blocks.
- Allowed outcomes: report hash/ref, attempt id, evaluator run ref/hash, k-trials stats, metric before/after/delta, affected gates, and candidate artifact ref are recorded.
- Forbidden outcomes: orphan proposals whose report, candidate artifact, metric delta, or affected gates cannot be reconstructed.

P5. Real report and MCP path, not synthetic helper-only records.

- User-visible promise: The AutoResearch validation/report pipeline emits the candidate overlay ref and metric before/after/delta needed for derivation, and the MCP tool can derive from `report_path` without an operator-supplied `candidate_changes` mapping.
- Public boundary: `validate_attempt` -> `build_autoresearch_report` -> `create_autoresearch_policy_proposals`.
- Allowed outcomes: report-derived `mode=report_derived` proposals when `candidate_changes` is omitted/`None`; backwards-compatible explicit helper mode for any supplied mapping, including `{}`.
- Forbidden outcomes: requiring the human/operator to author the proposal diff after the report has already accepted an evaluator-backed policy candidate.

## Implementation Decisions

- Keep `create_policy_evolution_proposals` as the lower-level explicit proposal helper and add a new report-derivation boundary above it.
- Extend AutoResearch attempt/validation/report payloads with `metric_before`, `metric_after`, `metric_delta`, `policy_overlay_candidate_ref`, and `policy_candidate_changes` so real reports provide the same evidence the deriver requires.
- Update `create_autoresearch_policy_proposals` so `candidate_changes` is optional: supplied values use the legacy explicit helper; omitted values invoke report derivation.
- Treat `_record_is_applyable` as the first eligibility filter so rejected, gaming-flagged, and invalid records cannot become applyable proposals.
- Require either `metric_delta` or enough before/after metric fields to prove a positive metric change before drafting a proposal.
- Reject contradictory metric provenance instead of trusting a positive explicit delta when before/after values disagree.
- Normalize candidate refs relative to the repo root and require the proposal target to be the Phase B overlay path.
- Support both report encodings produced by validation: a target-to-candidate `policy_candidate_changes` mapping and a direct `policy_overlay_candidate_ref`.
- Emit `autoresearch_policy_proposal_derivation_skipped` only for derivation records that fail after eligibility checks and emit `autoresearch_policy_proposal_created` only for draft proposals.

## Testing Decisions

- Use public-boundary tests against `derive_policy_evolution_proposals_from_report`, not only helper-level tests against explicit `candidate_changes`.
- Add an integration-style test that starts from `validate_attempt` and `build_autoresearch_report`, then derives a proposal without caller-authored changes.
- Add an MCP boundary test that calls `create_autoresearch_policy_proposals` without `candidate_changes` and verifies `mode=report_derived`.
- Assert the happy path returns one draft proposal with overlay diff, evaluator evidence, metric before/after/delta, report provenance, affected gates, and no-auto-apply flags.
- Assert gaming-flagged, rejected, non-evaluator-backed, zero-delta, negative-delta, missing-candidate, and non-overlay attempts produce no applyable proposal and do not mutate the overlay file.
- Keep the existing AutoResearch and policy-evolution tests green so the new deriver does not regress operator approval, rollback, or lower-level proposal behavior.

## Out Of Scope

- No operator approval UI changes.
- No automatic apply.
- No policy mutation by AutoResearch.
- No expansion beyond the Phase B overlay surface.
