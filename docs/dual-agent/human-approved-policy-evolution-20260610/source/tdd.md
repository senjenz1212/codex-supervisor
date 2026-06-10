# TDD Plan

## test_accepted_autoresearch_attempt_creates_policy_proposal_without_mutation

Maps to: Slice 1, P1, P2, P5.

RED: no proposal builder exists for an accepted evaluator-backed AutoResearch record.

GREEN: proposal creation writes `autoresearch_policy_proposal_created`, includes evaluator evidence and hashes, says authority is unchanged, and leaves target bytes unchanged.

## test_rejected_or_gaming_flagged_attempt_creates_no_applyable_policy_proposal

Maps to: Slice 2, P1, P2.

RED: rejected or gaming-flagged records can still produce applyable proposals.

GREEN: proposal creation returns no proposals for rejected records and accepted records with any gaming flags.

## test_non_evaluator_backed_or_mutating_attempt_creates_no_applyable_policy_proposal

Maps to: Slice 2, P1, P2, P5.

RED: accepted-looking records can create proposals without evaluator execution evidence or after mutating policy/gates.

GREEN: proposal creation returns no proposals unless the accepted record is evaluator-execution-backed and preserves `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

## test_approved_policy_proposal_applies_exact_recorded_candidate_and_records_hashes

Maps to: Slice 3, P3, P5.

RED: approval either has no helper or can write bytes that differ from the proposal.

GREEN: approval verifies current `before_hash`, writes candidate bytes whose hash equals `after_hash`, and records approver, approval channel, hashes, and rollback pointer.

## test_approval_rejects_stale_target_before_hash

Maps to: Slice 3, P3.

RED: approval can apply a proposal after the target artifact changed since proposal creation.

GREEN: approval raises `PolicyEvolutionError`, writes no approval event, and preserves the operator's drifted target bytes when current bytes no longer match the proposal `before_hash`.

## test_approval_and_denial_require_operator_identity_before_mutation_or_events

Maps to: Slice 3, P2, P3, P4, P5.

RED: approval or denial can proceed with a blank approver or blank approval channel.

GREEN: approval and denial raise `PolicyEvolutionError`, leave target bytes unchanged, and write no approval/denial events when operator identity or approval channel is missing.

## test_rollback_requires_operator_identity_before_mutation_or_events

Maps to: Slice 4, P4, P5.

RED: rollback can proceed with a blank approver or blank approval channel.

GREEN: rollback raises `PolicyEvolutionError`, leaves the approved target bytes unchanged, and writes no rollback event when operator identity or approval channel is missing.

## test_approval_rejects_tampered_candidate_after_hash

Maps to: Slice 3, P3.

RED: approval can apply a candidate artifact whose bytes no longer match the proposal `after_hash`.

GREEN: approval raises `PolicyEvolutionError`, writes no approval event, and leaves target bytes unchanged when candidate bytes are tampered after proposal creation.

## test_approval_rejects_post_write_hash_mismatch

Maps to: Slice 3, P3.

RED: approval records success or leaves corrupted target bytes if the bytes on disk after write do not hash to the recorded `after_hash`.

GREEN: approval raises `PolicyEvolutionError`, restores the previous target bytes, and writes no approval event if the post-write target hash differs from the proposal `after_hash`.

## test_approval_restores_prior_changes_when_later_apply_fails

Maps to: Slice 3, P3.

RED: a multi-file proposal can partially mutate earlier files if a later target write fails post-write hash verification.

GREEN: approval raises `PolicyEvolutionError`, restores every prepared target to its pre-approval bytes, and writes no approval event if any file in a multi-file proposal fails.

## test_denied_policy_proposal_records_denial_and_applies_nothing

Maps to: Slice 3, P4, P5.

RED: denied proposals have no ledger audit trail or mutate files.

GREEN: denial writes an event with approver/channel/reason and target bytes remain unchanged.

## test_policy_proposal_rollback_pointer_restores_previous_artifact

Maps to: Slice 4, P4.

RED: approved changes have no durable rollback pointer.

GREEN: rollback restores the previous bytes and records a rollback event with restored hash.

## test_autoresearch_policy_evolution_tools_apply_only_after_operator_approval

Maps to: Slice 3, Slice 4, P2, P3, P4, P5.

RED: proposal approval, denial, and rollback exist only as module helpers and are not available through the supervisor operator API/MCP surface.

GREEN: supervisor API/MCP tools create a proposal without mutation, deny without mutation, approve only with approver/channel, and roll back through the recorded pointer while preserving authority invariants.

## Regression Commands

- `.venv/bin/python -m pytest tests/test_autoresearch_policy_evolution.py tests/test_codex_supervisor_mcp_stdio.py::test_autoresearch_policy_evolution_tools_apply_only_after_operator_approval tests/test_codex_supervisor_mcp_stdio.py::test_codex_supervisor_mcp_exposes_dual_agent_gate_tools -q`
- `.venv/bin/python -m pytest tests/test_autoresearch_policy_evolution.py tests/test_autoresearch.py tests/test_stability_proposals.py -q`
- `.venv/bin/python -m pytest tests/test_dual_agent_workflow_driver.py tests/test_dual_agent_lead_invoker.py -q`
- `.venv/bin/python -m pytest -q`
- `git diff --check`
