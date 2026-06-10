# Implementation Plan

## Files / Modules To Touch

- `supervisor/autoresearch/policy_evolution.py`
- `supervisor/autoresearch/orchestrator.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `tests/test_autoresearch_policy_evolution.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
- `docs/dual-agent/human-approved-policy-evolution-20260610/source/prd.md`
- `docs/dual-agent/human-approved-policy-evolution-20260610/source/issues.md`
- `docs/dual-agent/human-approved-policy-evolution-20260610/source/tdd.md`

## Approach

Add a small policy-evolution module below AutoResearch. The module reads accepted AutoResearch report records, requires no gaming flags, verifies candidate artifact refs are listed in `changed_files`, computes before/after hashes and unified diffs, and emits non-mutating proposal events. Separate approval, denial, and rollback helpers require approver and approval channel, then write explicit ledger events. Expose those helpers through thin `CodexSupervisorMcpAPI` / MCP tools so operators can invoke the lifecycle without relying on direct Python imports.

## Risks

- Approval could apply stale bytes if the target changed after proposal creation, so approval must compare current bytes to the recorded `before_hash`.
- Multi-file approval could partially apply if a later write fails, so approval must restore all prepared targets and suppress approval events on any failure.
- Approval, denial, or rollback without operator identity would erase the audit boundary, so operator fields must be checked before any artifact write or event write.
- Proposal events could be confused with gate results, so payloads must explicitly preserve gate, reviewer panel, and typed outcome authority.
- Rollback could drift if it uses only a hash without stored bytes, so approval must store backup bytes and return a rollback pointer.

## Traceability

- P1 and P2 are covered by `test_accepted_autoresearch_attempt_creates_policy_proposal_without_mutation`, `test_rejected_or_gaming_flagged_attempt_creates_no_applyable_policy_proposal`, and `test_non_evaluator_backed_or_mutating_attempt_creates_no_applyable_policy_proposal`.
- P2/P3 operator provenance is covered by `test_approval_and_denial_require_operator_identity_before_mutation_or_events`.
- P4 rollback provenance is covered by `test_rollback_requires_operator_identity_before_mutation_or_events`.
- P3 is covered by `test_approved_policy_proposal_applies_exact_recorded_candidate_and_records_hashes`, `test_approval_rejects_stale_target_before_hash`, `test_approval_rejects_tampered_candidate_after_hash`, `test_approval_rejects_post_write_hash_mismatch`, and `test_approval_restores_prior_changes_when_later_apply_fails`.
- P4 is covered by `test_denied_policy_proposal_records_denial_and_applies_nothing` and `test_policy_proposal_rollback_pointer_restores_previous_artifact`.
- P5 is covered by authority invariant assertions in `test_accepted_autoresearch_attempt_creates_policy_proposal_without_mutation`, `test_approved_policy_proposal_applies_exact_recorded_candidate_and_records_hashes`, and `test_denied_policy_proposal_records_denial_and_applies_nothing`.
- The supervisor operator boundary is covered by `test_autoresearch_policy_evolution_tools_apply_only_after_operator_approval`.
