# Dual-Agent Slice 0 Coverage Index

Source PRD: `docs/prd/dual-agent-slice0-reality-check-prd.md`

## Coverage Map

| PRD area | Probe/test coverage | Status |
|---|---|---|
| Day 0 cockpit prerequisite | `docs/adr/0002-current-cortex-cockpit-for-dual-agent-slice0.md` selects current Cortex | Covered |
| Hard-stop rule includes P0-P4 and requires P4 pause evidence | `test_hard_stop_summary_blocks_until_all_required_probes_are_green`, `test_hard_stop_summary_requires_p4_pause_evidence_not_just_green_p4` | Covered |
| P0 credential boundary | `test_p0_credential_boundary_checks_gateway_precedence_and_redaction`, `test_p0_fails_when_successful_model_call_hits_wrong_gateway`, `test_p0_fails_without_required_spawn_env_evidence` | Covered |
| P1 worktree boundary | `test_p1_worktree_boundary_requires_expected_cwd_and_no_offlimits_touch`, `test_p1_fails_when_outcome_was_not_written`, `test_p1_fails_when_git_status_contains_unexpected_path`, `test_p1_fails_when_claude_touches_sibling_worktree` | Covered |
| P2 worker orchestration invocation and high-volume capture | `test_p2_worker_invocation_requires_complete_high_volume_capture`, `test_p2_fails_on_truncated_capture_even_if_worker_exits_zero`, `test_p2_fails_when_output_was_not_codex_spawned_or_no_surface_is_recorded`, `test_p2_fails_when_load_case_is_not_high_volume_tokens` | Covered |
| P3 worker output to outcome fidelity | `test_p3_worker_outcome_adapter_preserves_specialists_decisions_and_objections`, `test_p3_fails_when_adapter_drops_worker_signal`, `test_p3_fails_when_required_review_evidence_is_not_explicit` | Covered |
| P4 deadlock pauses | `test_p4_deadlock_budget_records_pause_not_auto_decision` | Covered |
| P5 artifact exposure guardrail | `test_p5_artifact_redaction_covers_markdown_and_gate_logs` | Covered |
| P6 test coverage gate | `test_p6_test_coverage_gate_asks_one_bounded_followup_for_code_without_tests`, `test_p6_test_coverage_gate_passes_when_test_file_changed` | Covered |
| P7 Telegram rate limit and batching | `test_p7_telegram_batches_fyis_but_sends_alerts_approvals_and_milestones` | Covered |
| P8 computer-use validation smoke | Manual/live smoke only; intentionally not in deterministic suite | Deferred |
| P9 ChatGPT mobile control smoke | Manual/live smoke only; intentionally not in deterministic suite | Deferred |
| P10 parallel isolation | `test_p10_parallel_isolation_requires_task_prefix_and_unique_worktree`, `test_p10_fails_on_shared_worktree_or_ambiguous_telegram` | Covered |
| P11 claim verification | `test_p11_claim_verification_flags_unverified_agent_claims` | Covered |
| Desktop GUI live reflection retired | `test_hard_stop_summary_blocks_until_all_required_probes_are_green` asserts `history_only` | Covered |

## Public Boundary

The deterministic Slice 0 boundary is `supervisor.dual_agent`: fixture-shaped
probe inputs enter pure validators and produce `ProbeResult` records. Live
Claude/Codex/Telegram/ChatGPT/Desktop probes must be adapted into these
fixture shapes before they can unblock CS24. Secret handling in Slice 0 is a
lightweight exposure guardrail for operator-facing summaries, not an exhaustive
DLP program.

## Local Claude Code Surface Evidence

- `claude agents` is available locally and reports configured agents.
- No local `/lead` command file has been found in user or plugin command paths.
- P2 therefore treats `/lead` as one acceptable orchestration surface, but the
  practical local target is Claude Code agents plus installed
  subagent-driven-development/feature-dev/code-review skills.
- Current live-probe notes live in
  `docs/testing/dual-agent-slice0-live-evidence.md`.

## Deferred Live Checks

- P8 requires a deterministic local GUI or browser target and should not block
  non-GUI delivery.
- P9 requires Sam's phone and connected-host setup; it is a product-surface
  smoke check, not a hard-stop probe.
- Day 0 cockpit ADR selects current Cortex as the first cockpit target; later
  replacement can happen behind the same supervisor-ledger contract.
