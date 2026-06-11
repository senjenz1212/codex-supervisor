# Issues: AutoResearch Policy Diff Derivation

## Slice C1: Derive Overlay Proposal From Accepted Report

Priority: P1
Estimate: medium
Scope: Add `derive_policy_evolution_proposals_from_report` that scans accepted report records, requires evaluator-backed positive metric delta, derives the candidate overlay artifact, and produces a draft proposal targeting `.supervisor/policy-overlay.yaml`.

Acceptance criteria:

- [ ] Positive accepted report drafts exactly one proposal without caller-supplied `candidate_changes`.
- [ ] Proposal includes overlay diff and full provenance.
- [ ] Proposal is `status=draft` and operator approval remains required.

PRD promises: P1, P3, P4.

## Slice C2: Reject Non-Applyable Or Non-Overlay Reports

Priority: P1
Estimate: medium
Scope: Ensure rejected, gaming-flagged, non-evaluator-backed, zero/negative-delta, missing-candidate, and non-overlay candidate records produce no applyable proposal.

Acceptance criteria:

- [ ] Gaming-flagged, rejected, non-evaluator-backed, missing-candidate, and negative/zero-delta attempts draft nothing applyable.
- [ ] Derived non-overlay candidate paths are rejected at derivation, including direct `policy_overlay_candidate_ref` values.
- [ ] Authority flags remain false for all no-op paths.

PRD promises: P1, P2, P3.

## Slice C3: Wire Real Report And MCP Derivation Path

Priority: P1
Estimate: medium
Scope: Carry policy candidate refs and metric before/after/delta through `validate_attempt` and `build_autoresearch_report`, then let `create_autoresearch_policy_proposals` derive proposals from `report_path` when `candidate_changes` is omitted.

Acceptance criteria:

- [ ] Validation reports preserve `policy_candidate_changes` and direct `policy_overlay_candidate_ref` provenance.
- [ ] Reports compute `metric_after` and `metric_delta` from evaluator trials plus a supplied baseline metric.
- [ ] Contradictory explicit metric provenance is rejected before proposal creation.
- [ ] The MCP tool returns `mode=report_derived` and drafts one proposal without operator-authored `candidate_changes`.

PRD promises: P1, P3, P4, P5.

## Coverage Index

- P1: Slice C1, C2, and C3.
- P2: Slice C2.
- P3: Slice C1, C2, and C3.
- P4: Slice C1 and C3.
- P5: Slice C3.
