# Implementation Plan: AutoResearch Policy Diff Derivation

## Boundary

Add `derive_policy_evolution_proposals_from_report` in `supervisor/autoresearch/policy_evolution.py` as the Phase C public boundary. Existing `create_policy_evolution_proposals` remains the lower-level helper for explicit candidate-change proposals.

## Files / Modules To Touch

- `supervisor/autoresearch/policy_evolution.py`: add the report-derivation boundary, positive metric delta checks, overlay candidate derivation, draft-only provenance, and skip event emission.
- `supervisor/autoresearch/schema.py`: carry optional metric delta and policy candidate provenance from attempts into validation reports.
- `supervisor/autoresearch/validation.py`: compute metric before/after/delta fields and validate that policy candidate refs point at changed artifacts.
- `supervisor/autoresearch/orchestrator.py`: register the derivation-skipped event kind so the ledger can store rejected derivation attempts.
- `mcp_tools/codex_supervisor_stdio.py`: make the proposal tool derive from report evidence when `candidate_changes` is omitted, while retaining the explicit helper path for backwards compatibility.
- `tests/test_autoresearch_policy_evolution.py`: add public-boundary tests for derivation, rejection, and no-auto-apply invariants.
- `tests/test_autoresearch.py`: assert real AutoResearch reports carry derivation provenance.
- `tests/test_codex_supervisor_mcp_stdio.py`: assert the MCP proposal tool can draft from `report_path` without operator-authored changes.
- `docs/dual-agent/autoresearch-policy-diff-derivation-20260610/source/prd.md`: keep PRD promise contracts and gate-readable product requirements for Phase C.
- `docs/dual-agent/autoresearch-policy-diff-derivation-20260610/source/tdd.md`: keep the TDD mapping from promises to concrete regression tests.

## Steps

1. Derive candidate overlay artifacts from accepted report records:
   - require `_record_is_applyable`;
   - require positive metric delta from `metric_delta` or before/after fields;
   - reject contradictory explicit deltas where `metric_delta` does not match `metric_after - metric_before`;
   - derive the candidate overlay ref from report evidence, not a caller argument.
2. Call the existing proposal builder only with target `.supervisor/policy-overlay.yaml`.
3. Mark derived proposals as `status=draft`, `source=autoresearch_deriver`, and preserve all no-auto-apply authority flags.
4. Add a replayable `derivation` block with report ref/hash, attempt id, candidate ref, affected gates, metric before/after/delta.
5. Emit `autoresearch_policy_proposal_created` for drafted proposals and `autoresearch_policy_proposal_derivation_skipped` for post-eligibility derivation records that fail with a `PolicyEvolutionError` when a state/run is supplied; pre-eligibility records remain filtered out as non-applyable evidence.
6. Propagate real report provenance by adding optional `metric_before`, `metric_after`, `metric_delta`, `policy_overlay_candidate_ref`, and `policy_candidate_changes` fields to attempts and validation reports.
7. Let validation compute `metric_after` from the trial median and `metric_delta` from before/after when the attempt supplies a baseline metric.
8. Support both report encodings: `policy_candidate_changes` mapping and direct `policy_overlay_candidate_ref`.
9. Change the MCP proposal boundary so `candidate_changes` is optional: `None`/omitted means derive from report evidence; any supplied mapping, including `{}`, uses the legacy explicit helper path.

## Risks

- The deriver could accidentally trust caller-authored arbitrary target paths if candidate-change normalization is not pinned to the Phase B overlay path.
- A report with accepted status but no metric delta could create low-signal proposals unless the metric before/after/delta proof is mandatory.
- Draft-only authority can regress if derived proposals reuse apply helpers without reasserting `requires_operator_approval`, `default_change_allowed=false`, and `gate_advanced=false`.
- Skip provenance should not be confused with acceptance: rejected records can emit evidence, but they must never become applyable proposals.
- Public tool compatibility can regress if optional `candidate_changes` changes the explicit proposal path; keep that mode and add a separate report-derived mode.

## Tests

- `test_accepted_report_derives_overlay_policy_proposal_without_candidate_changes_input`
- `test_deriver_skips_gaming_flagged_and_non_positive_metric_reports`
- `test_deriver_rejects_inconsistent_explicit_metric_delta`
- `test_deriver_skips_rejected_and_non_evaluator_backed_records_at_public_boundary`
- `test_deriver_rejects_missing_candidate_artifact_with_skip_event`
- `test_deriver_rejects_direct_non_overlay_candidate_ref_at_derivation`
- `test_deriver_rejects_non_overlay_candidate_at_derivation`
- `test_derived_proposal_still_requires_operator_approval`
- `test_validation_report_pipeline_derives_policy_proposal_without_operator_authored_changes`
- `test_validation_report_derives_from_direct_policy_overlay_candidate_ref`
- `test_autoresearch_report_carries_policy_derivation_fields`
- `test_autoresearch_policy_proposal_tool_derives_from_report_without_candidate_changes`
- `test_autoresearch_policy_proposal_tool_empty_candidate_changes_stays_explicit`

Focused regression run:

```text
.venv/bin/python -m pytest tests/test_autoresearch_policy_evolution.py tests/test_autoresearch_generator.py tests/test_autoresearch.py tests/test_codex_supervisor_mcp_stdio.py::test_autoresearch_policy_proposal_tool_derives_from_report_without_candidate_changes -q
```

## Traceability

- P1 is covered by `test_accepted_report_derives_overlay_policy_proposal_without_candidate_changes_input`, `test_deriver_skips_gaming_flagged_and_non_positive_metric_reports`, `test_deriver_skips_rejected_and_non_evaluator_backed_records_at_public_boundary`, and `test_deriver_rejects_missing_candidate_artifact_with_skip_event`.
- P2 is covered by `test_deriver_rejects_missing_candidate_artifact_with_skip_event`, `test_deriver_rejects_direct_non_overlay_candidate_ref_at_derivation`, and `test_deriver_rejects_non_overlay_candidate_at_derivation`.
- P3 is covered by `test_accepted_report_derives_overlay_policy_proposal_without_candidate_changes_input`, `test_deriver_skips_gaming_flagged_and_non_positive_metric_reports`, `test_deriver_skips_rejected_and_non_evaluator_backed_records_at_public_boundary`, `test_deriver_rejects_missing_candidate_artifact_with_skip_event`, `test_deriver_rejects_direct_non_overlay_candidate_ref_at_derivation`, and `test_derived_proposal_still_requires_operator_approval`.
- P4 is covered by `test_accepted_report_derives_overlay_policy_proposal_without_candidate_changes_input`.
- P5 is covered by `test_validation_report_pipeline_derives_policy_proposal_without_operator_authored_changes`, `test_validation_report_derives_from_direct_policy_overlay_candidate_ref`, `test_autoresearch_report_carries_policy_derivation_fields`, `test_autoresearch_policy_proposal_tool_derives_from_report_without_candidate_changes`, and `test_autoresearch_policy_proposal_tool_empty_candidate_changes_stays_explicit`.
