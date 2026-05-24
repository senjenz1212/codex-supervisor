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
| P2 high-volume replay fixture family | `test_p2_replay_fixture_family_exercises_large_stdout_without_truncation` covers 2K, 10K, 50K, and 200K token-size replay through the same `/lead` invoker | Covered |
| P3 worker output to outcome fidelity | `test_p3_worker_outcome_adapter_preserves_specialists_decisions_and_objections`, `test_p3_fails_when_adapter_drops_worker_signal`, `test_p3_fails_when_required_review_evidence_is_not_explicit` | Covered |
| Claude `/lead` invocation wrapper | `test_build_lead_command_uses_non_bare_claude_so_slash_lead_can_resolve`, `test_select_lead_model_prefers_best_models_for_gate_decisions`, `test_invoke_claude_lead_parses_json_output_and_validates_outcome`, `test_invoke_claude_lead_reports_subprocess_failure`, `test_invoke_claude_lead_reports_timeout_without_auto_retry`, `test_invoke_claude_lead_fails_loudly_on_claude_json_schema_drift`, `test_invoke_claude_lead_reports_malformed_outcome_block`, `test_invoke_claude_lead_surfaces_outcome_fidelity_failure` | Covered |
| Typed Codex-to-Claude handoff packet | `test_write_handoff_packet_pins_schema_planning_checksums_and_lead_skill`, `test_build_lead_prompt_references_handoff_packet_instead_of_raw_context`, `test_handoff_packet_rejects_planning_artifacts_outside_worktree`, `test_custom_outcome_validation_policy_is_pinned_in_packet`, `test_verify_planning_artifact_boundaries_detects_worker_spec_rewrite` | Covered |
| Planning artifact substance gate | `test_planning_validator_fixture_matrix_accepts_good_and_rejects_stub_or_sneaky`, `test_planning_validator_blocks_unresolved_plan_traceability`, `test_run_dual_agent_gate_blocks_stub_prd_before_claude_invocation`, `test_start_dual_agent_gate_relaxed_artifact_policy_still_blocks_stub_planning`, `test_read_gate_transcript_includes_planning_validation_receipts`, `test_run_dual_agent_workflow_blocks_auto_seeded_planning_stubs` prove PRD/issues/TDD/grill/implementation-plan artifacts are deterministic, non-stub, traceable, ledger-receipted, and enforced below relaxed MCP artifact preflight before `/lead` invocation. | Covered |
| P4 deadlock pauses | `test_p4_deadlock_budget_records_pause_not_auto_decision` | Covered |
| P4 Telegram deadlock escalation | `test_p4_deadlock_escalation_sends_telegram_prompt_and_callback_resolves` creates a `kill_or_pause` ask, sends approval buttons, and resolves a callback to `continue_requested` | Covered |
| Validation-failure escalation policies | `test_blocked_gate_abort_policies_escalate_validation_failures`, `test_validation_failure_callback_resolves_retry_request` cover fidelity failure, subprocess failure, timeout, schema drift, and Telegram callback resolution | Covered |
| Resume loop | `test_deadlock_continue_signal_is_claimed_once_and_redispatches_gate` proves `Continue` is claimed exactly once and re-dispatches the matching gate spec | Covered |
| Stale paused-state digest | `test_paused_dual_agent_actions_send_one_stale_digest` proves paused dual-agent actions emit one Telegram digest after the stale threshold and remain paused | Covered |
| CS24 gate runner | `test_cs24_gate_runner_writes_handoff_invokes_lead_and_verifies_planning_boundaries`, `test_cs24_gate_runner_retries_malformed_outcome_once_then_accepts`, `test_cs24_gate_runner_stops_sequence_on_blocked_gate` | Covered |
| CS24 handoff/worktree lock | `test_cs24_gate_runner_refuses_existing_handoff_lock_without_invoking_lead`, `test_cs24_gate_runner_removes_handoff_lock_after_success`, `test_cs24_gate_runner_removes_handoff_lock_after_blocked_worker_result` prove one live gate owns the worktree handoff boundary and stale locks do not remain after normal blocked or accepted exits | Covered |
| Codex-facing MCP control surface | `test_codex_supervisor_mcp_exposes_dual_agent_gate_tools`, `test_codex_supervisor_mcp_reads_clean_gate_transcript`, `test_codex_supervisor_mcp_records_rounds_checks_budget_and_polls_resume`, `test_codex_supervisor_mcp_start_codex_session_can_dry_run_or_execute_with_runner`, `test_codex_supervisor_mcp_console_script_is_registered` | Covered |
| Supervisor-owned workflow lifecycle | `test_run_dual_agent_workflow_happy_path_owns_full_lifecycle`, `test_run_dual_agent_workflow_enforces_v5_without_prompt_following`, `test_run_dual_agent_workflow_retries_malformed_outcome_once`, `test_run_dual_agent_workflow_blocks_on_claude_failure_and_requests_input`, `test_run_dual_agent_workflow_can_rerun_after_corrective_input`, `test_run_dual_agent_workflow_blocks_user_facing_without_visual_evidence`, `test_run_dual_agent_workflow_auto_requires_visual_evidence_for_vela_live_surfaces`, `test_run_dual_agent_workflow_auto_visual_policy_accepts_computer_use_evidence`, `test_run_dual_agent_workflow_verifies_final_claims`, `test_workflow_resume_prompt_tool_is_state_derived` | Covered |
| PRD/TDD skill execution receipts | `test_run_dual_agent_workflow_requires_prd_tdd_skill_receipts`, `test_run_dual_agent_workflow_records_skill_receipt_validation`, `test_read_gate_transcript_includes_skill_receipt_validation` prove `run_dual_agent_workflow` blocks at `workflow_start` unless passing `skill_run` receipts cover `to_prd`, `prd_grill`, `to_issues`, `tdd`, and `tdd_grill`. | Covered |
| Failure taxonomy and trace envelope | `test_failure_taxonomy_classifies_receipt_and_skill_gaps`, `test_blocking_probe_failure_includes_planning_and_claim_probes_but_not_p4`, `test_trace_envelope_stamps_dual_agent_payloads_without_wrapping_original_shape`, `test_blocking_red_probe_beats_accepted_completion` prove dual-agent events carry a deterministic MAST-inspired failure category and trace envelope while preserving existing payload fields. | Covered |
| Codex rich reviewer packet | `test_codex_confidence_report_and_review_packet_explain_decision`, `test_agent_mailbox_message_carries_trace_fields`, workflow driver round tests, and artifact export tests prove Codex emits a structured per-gate review packet with requirements, evidence refs, confidence criteria, and would-change-if criteria. | Covered |
| Optional Cursor tri-agent reviewer | `test_build_cursor_prompt_is_review_only_and_uses_typed_outcome_contract`, `test_cursor_accepts_requires_green_probe_and_accept_decision`, `test_select_cursor_model_defaults_to_documented_composer_model`, `test_run_dual_agent_workflow_with_cursor_review_accepts_all_gates`, `test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection` | Covered at fixture boundary |
| Codex Desktop no-Telegram gate flow | `test_dual_agent_skill_uses_desktop_chat_when_telegram_is_absent`, `test_desktop_probe_doc_covers_g1_g2_g3_and_corrects_execute_flag`, `docs/testing/codex-desktop-dual-agent-probes.md` | Manual probes scripted |
| P5 artifact exposure guardrail | `test_p5_artifact_redaction_covers_markdown_and_gate_logs` | Covered |
| P6 test coverage gate | `test_p6_test_coverage_gate_asks_one_bounded_followup_for_code_without_tests`, `test_p6_test_coverage_gate_passes_when_test_file_changed` | Covered |
| P7 Telegram rate limit and batching | `test_p7_telegram_batches_fyis_but_sends_alerts_approvals_and_milestones` | Covered |
| P8 computer-use validation evidence gate | `test_codex_supervisor_mcp_blocks_user_facing_gate_without_visual_validation`, `test_codex_supervisor_mcp_accepts_user_facing_gate_with_screenshots`, `test_run_dual_agent_workflow_auto_requires_visual_evidence_for_vela_live_surfaces`, `test_run_dual_agent_workflow_auto_visual_policy_accepts_computer_use_evidence`, `test_export_dual_agent_run_artifacts_copies_screenshots_and_writes_manifest` require valid screenshot files, Browser/Computer Use provenance, passed visual review metadata, and exported evidence. Vela/Slack/Calendar/live-provider workflows auto-upgrade to visual-evidence-required even if the caller omits `user_facing=True`. | Covered at evidence boundary |
| P9 ChatGPT mobile control smoke | Manual/live smoke only; intentionally not in deterministic suite | Deferred |
| P10 parallel isolation | `test_p10_parallel_isolation_requires_task_prefix_and_unique_worktree`, `test_p10_fails_on_shared_worktree_or_ambiguous_telegram` | Covered |
| P11 claim verification | `test_p11_claim_verification_flags_unverified_agent_claims` | Covered |
| Desktop GUI live reflection retired | `test_hard_stop_summary_blocks_until_all_required_probes_are_green` asserts `history_only` | Covered |

## Public Boundary

The deterministic Slice 0 boundary is `supervisor.dual_agent`: fixture-shaped
probe inputs enter pure validators and produce `ProbeResult` records. The live
Claude `/lead` process boundary is `supervisor.dual_agent_lead`; tests inject a
fake runner and assert the command/prompt/result adaptation without calling live
Claude. The same boundary writes `.handoff/<task_id>.json` packets with schema
version, planning artifact checksums, outcome-validation policy, and `/lead`
skill version/hash pinning. The CS24 runner boundary is
`supervisor.dual_agent_runner`: it writes the handoff packet, invokes `/lead`,
verifies P1/P2/P3 evidence, retries malformed outcomes once according to packet
policy, stops on blocked gates, escalates validation failures according to the
packet policy, routes budget deadlock to Telegram through the existing
ask/callback path, exposes a pull-based resume loop for `Continue` answers, and
emits one-time stale digests for paused dual-agent actions. It also holds a
worktree-level `.handoff/.dual-agent.lock` while a gate is invoking `/lead` so
another gate cannot concurrently overwrite the same handoff boundary or mutate
the same worktree through the runner. `run_dual_agent_workflow` adds the
supervisor-owned lifecycle layer: source artifact creation, ordered gate
execution, max-round enforcement, workflow state tables, mandatory artifact
export, claim checks, milestone events, v5 deadlock escalation, and state-derived resume prompts. It can
optionally run Cursor as a third reviewer/challenger through the `cursor-sdk`
boundary, while Claude remains the `/lead` implementer and the supervisor
remains the acceptance boundary. Planning artifact substance is now enforced
inside the runner: explicit PRD/issues/TDD/grill/implementation-plan paths must
pass deterministic, LLM-free checks before `/lead` is invoked, and
`dual_agent_planning_validation` receipts are included in the ledger and
Codex-facing transcript. `run_dual_agent_workflow` additionally requires
`skill_run` receipts for the PRD-to-TDD skill chain by default, writes
`dual_agent_skill_receipt_validation`, and stamps dual-agent events with a
non-breaking `trace_envelope` that includes policy verdict and deterministic
failure taxonomy. Live
Claude/Codex/Telegram/ChatGPT/Desktop probes must be adapted into the same
fixture shapes before they can unblock CS24. Secret handling in Slice 0 is a
lightweight exposure guardrail for operator-facing summaries, not an exhaustive
DLP program.

Codex MCP support is live-probed in
`docs/testing/codex-mcp-support-probe.md`. The probe showed `codex exec --json`
loading a repo-provided stdio MCP server and invoking its tool. The production
entrypoint is `codex-supervisor-mcp`.

Codex Desktop narrow-scope probes are scripted in
`docs/testing/codex-desktop-dual-agent-probes.md`. In this scope, Telegram is
not the escalation surface; blocked gates ask the user in Desktop chat and then
re-run `start_dual_agent_gate` with corrective input.

## Local Claude Code Surface Evidence

- A portable global `/lead` command is installed at
  `/Users/sam.zhang/.claude/skills/lead/SKILL.md`.
- `/lead` works from `codex-supervisor` and `muse-editor` through non-bare
  `claude -p`; `--bare` does not resolve slash commands.
- Unity Hub has a richer project-local `/lead` override under ignored
  `.claude/skills` operational config.
- `claude agents` is available locally and remains the fallback worker surface
  if `/lead` is absent in another environment.
- Current live-probe notes live in
  `docs/testing/dual-agent-slice0-live-evidence.md`.

## Deferred Live Checks

- P8 now blocks user-facing strict gates unless Codex supplies visual evidence
  captured through Browser or Computer Use and marks the visual review as passed.
  The remaining live check is whether Codex actually captures the UI correctly
  for a given product surface; the supervisor enforces the evidence contract.
- Live Cursor SDK probe support lives in `scripts/probe_cursor_sdk_live.py`.
  The script writes a diagnostic fixture even when `CURSOR_API_KEY` is absent;
  a green live Cursor run remains deferred until the key is present in the
  process environment.
- P9 requires Sam's phone and connected-host setup; it is a product-surface
  smoke check, not a hard-stop probe.
- Day 0 cockpit ADR selects current Cortex as the first cockpit target; later
  replacement can happen behind the same supervisor-ledger contract.
- Paused-state stale digests are covered at the runner boundary and owned by
  the Telegram poller sweep.
