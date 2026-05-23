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
| P4 deadlock pauses | `test_p4_deadlock_budget_records_pause_not_auto_decision` | Covered |
| P4 Telegram deadlock escalation | `test_p4_deadlock_escalation_sends_telegram_prompt_and_callback_resolves` creates a `kill_or_pause` ask, sends approval buttons, and resolves a callback to `continue_requested` | Covered |
| Validation-failure escalation policies | `test_blocked_gate_abort_policies_escalate_validation_failures`, `test_validation_failure_callback_resolves_retry_request` cover fidelity failure, subprocess failure, timeout, schema drift, and Telegram callback resolution | Covered |
| Resume loop | `test_deadlock_continue_signal_is_claimed_once_and_redispatches_gate` proves `Continue` is claimed exactly once and re-dispatches the matching gate spec | Covered |
| Stale paused-state digest | `test_paused_dual_agent_actions_send_one_stale_digest` proves paused dual-agent actions emit one Telegram digest after the stale threshold and remain paused | Covered |
| CS24 gate runner | `test_cs24_gate_runner_writes_handoff_invokes_lead_and_verifies_planning_boundaries`, `test_cs24_gate_runner_retries_malformed_outcome_once_then_accepts`, `test_cs24_gate_runner_stops_sequence_on_blocked_gate` | Covered |
| Codex-facing MCP control surface | `test_codex_supervisor_mcp_exposes_dual_agent_gate_tools`, `test_codex_supervisor_mcp_reads_clean_gate_transcript`, `test_codex_supervisor_mcp_records_rounds_checks_budget_and_polls_resume`, `test_codex_supervisor_mcp_start_codex_session_can_dry_run_or_execute_with_runner`, `test_codex_supervisor_mcp_console_script_is_registered` | Covered |
| Codex Desktop no-Telegram gate flow | `test_dual_agent_skill_uses_desktop_chat_when_telegram_is_absent`, `test_desktop_probe_doc_covers_g1_g2_g3_and_corrects_execute_flag`, `docs/testing/codex-desktop-dual-agent-probes.md` | Manual probes scripted |
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
emits one-time stale digests for paused dual-agent actions. Live
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

- P8 requires a deterministic local GUI or browser target and should not block
  non-GUI delivery.
- P9 requires Sam's phone and connected-host setup; it is a product-surface
  smoke check, not a hard-stop probe.
- Day 0 cockpit ADR selects current Cortex as the first cockpit target; later
  replacement can happen behind the same supervisor-ledger contract.
- Paused-state stale digests are covered at the runner boundary and owned by
  the Telegram poller sweep.
