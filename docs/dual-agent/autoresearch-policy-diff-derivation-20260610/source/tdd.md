# TDD Plan: AutoResearch Policy Diff Derivation

## test_accepted_report_derives_overlay_policy_proposal_without_candidate_changes_input

Maps to: Slice C1, P1/P3/P4.

Red: An accepted report requires a caller-authored `candidate_changes` mapping to create a proposal.

Green: Calling `derive_policy_evolution_proposals_from_report` with only the report, repo root, and affected gates returns one draft overlay proposal with diff, evaluator evidence, evaluator run ref/hash, attempt id, affected gates, metric before/after/delta, report provenance, and no-auto-apply flags.

## test_deriver_skips_gaming_flagged_and_non_positive_metric_reports

Maps to: Slice C2, P1/P3.

Red: Accepted records with gaming flags or zero/negative metric delta still create proposals.

Green: The deriver returns no applyable proposals for gaming-flagged, zero-delta, and negative-delta records, and emits no approval/apply events.

## test_deriver_rejects_inconsistent_explicit_metric_delta

Maps to: Slice C2/C3, P1/P4/P5.

Red: A report can claim a positive explicit `metric_delta` while `metric_after - metric_before` is negative or different, and the deriver trusts the contradictory delta.

Green: The deriver emits `autoresearch_policy_proposal_derivation_skipped` with a metric-consistency reason and drafts no proposal.

## test_deriver_skips_rejected_and_non_evaluator_backed_records_at_public_boundary

Maps to: Slice C2, P1/P3.

Red: Rejected reports, fixture metrics, or missing evaluator run refs still draft proposals because lower-level helper eligibility is not exercised at the deriver boundary.

Green: Calling `derive_policy_evolution_proposals_from_report` with rejected, non-evaluator-backed, missing-run-ref, and missing-run-hash records returns no proposal, writes no proposal-created events, and leaves the overlay bytes unchanged.

## test_deriver_rejects_missing_candidate_artifact_with_skip_event

Maps to: Slice C2, P1/P2/P3/P4.

Red: An otherwise accepted report without a resolvable candidate overlay artifact drafts a proposal or silently disappears without replayable skip evidence.

Green: The deriver returns no proposal, emits `autoresearch_policy_proposal_derivation_skipped` with a missing-candidate reason, records no policy mutation, and leaves the live overlay unchanged.

## test_deriver_rejects_direct_non_overlay_candidate_ref_at_derivation

Maps to: Slice C2, P2/P3.

Red: An accepted report sets `policy_overlay_candidate_ref` to a non-overlay file such as `candidates/execution.md`, and the deriver forces that arbitrary artifact into a `.supervisor/policy-overlay.yaml` proposal target.

Green: The deriver rejects the direct candidate ref before proposal creation, emits a derivation-skipped reason naming the non-overlay candidate artifact, and leaves both the live overlay and candidate file unchanged.

## test_deriver_rejects_non_overlay_candidate_at_derivation

Maps to: Slice C2, P2/P3.

Red: A report candidate touching `prompts/execution.md` or another non-overlay surface is converted into a proposal and relies on apply-time rejection.

Green: The deriver rejects the record before proposal creation, records a derivation-skipped reason when state is supplied, and applies no mutation.

## test_derived_proposal_still_requires_operator_approval

Maps to: Slice C1, P3.

Red: Derived proposals are marked approved or mutate `.supervisor/policy-overlay.yaml`.

Green: The proposal is draft-only with `requires_operator_approval=true`, `default_change_allowed=false`, `automatic_policy_mutation=false`, `gate_advanced=false`, and the overlay file remains unchanged.

## test_validation_report_pipeline_derives_policy_proposal_without_operator_authored_changes

Maps to: Slice C3, P1/P4/P5.

Red: The deriver only passes with synthetic records and real `validate_attempt` / `build_autoresearch_report` output omits metric delta and policy candidate refs.

Green: A validated evaluator-backed attempt that records `metric_before` and `policy_candidate_changes` emits a report record with `metric_before`, computed `metric_after`, computed `metric_delta`, and the candidate overlay mapping; passing that report into `derive_policy_evolution_proposals_from_report` drafts one overlay proposal without operator-authored changes.

## test_validation_report_derives_from_direct_policy_overlay_candidate_ref

Maps to: Slice C3, P1/P4/P5.

Red: A valid report that carries `policy_overlay_candidate_ref` but no `policy_candidate_changes` is rejected before the deriver reaches the direct candidate ref.

Green: The validation/report pipeline preserves the direct candidate ref, and the deriver drafts one overlay proposal from it without requiring `candidate_changes`.

## test_autoresearch_report_carries_policy_derivation_fields

Maps to: Slice C3, P4/P5.

Red: AutoResearch report records do not preserve the policy candidate and metric delta provenance needed to reconstruct a proposal.

Green: The report record contains stable `policy_candidate_changes`, `metric_before`, `metric_after`, and `metric_delta` fields from validation output.

## test_autoresearch_policy_proposal_tool_derives_from_report_without_candidate_changes

Maps to: Slice C3, P1/P3/P5.

Red: The MCP operator tool requires a human-supplied `candidate_changes` mapping, so the human still authors the diff.

Green: Calling `create_autoresearch_policy_proposals` with only `report_path`, `repo_root`, and `affected_gates` returns `mode=report_derived`, drafts one proposal, and leaves `.supervisor/policy-overlay.yaml` unchanged.

## test_autoresearch_policy_proposal_tool_empty_candidate_changes_stays_explicit

Maps to: Slice C3, P5.

Red: The MCP tool treats explicitly supplied `candidate_changes={}` as omitted and derives from report evidence, erasing the legacy explicit-helper path.

Green: `candidate_changes=None` is the report-derived mode, while `candidate_changes={}` stays in `mode=explicit_candidate_changes`, creates zero proposals, writes no proposal events, and mutates nothing.
