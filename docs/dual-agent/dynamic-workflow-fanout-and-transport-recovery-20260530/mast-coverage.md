# MAST Coverage

This matrix lists every deterministic MAST-inspired mode the supervisor knows how to classify, plus whether the current run observed it.

| code | category | mode | live_status | deterministic_status | trigger_surface | entrypoint | deterministic_probe | observed_sources |
|---|---|---|---|---|---|---|---|---|
| FM-1.1 | Specification Issues | Disobey task specification | not_observed_in_run | covered_by_deterministic_probe | payload | failure_taxonomy_for_payload | blocked payload reason `disobey_task_spec` or planning artifact failure | [] |
| FM-1.2 | Specification Issues | Disobey role specification | not_observed_in_run | covered_by_deterministic_probe | payload | failure_taxonomy_for_payload -> classify_failure | blocked payload reason `role_violation_covenant` | [] |
| FM-1.3 | Specification Issues | Step repetition | observed_in_run | covered_by_deterministic_probe | sequence | detect_sequence_failures | same gate and handoff packet observed twice | [{"details": {"signature": ["tdd_review", "/Users/sam.zhang/Documents/codex-supervisor/.handoff/dynamic-workflow-fanout-and-transport-recovery-20260530.json"]}, "event_ids": [305428, 305434], "source": "sequence"}] |
| FM-1.4 | Specification Issues | Loss of conversation history | not_observed_in_run | covered_by_deterministic_probe | payload | classify_failure | blocked payload reason `lost_conversation_history` | [] |
| FM-1.5 | Specification Issues | Unaware of termination conditions | observed_in_run | covered_by_deterministic_probe | payload | classify_failure | blocked payload reason `agents_not_converged_max_rounds` | [{"event_id": 305434, "event_kind": "dual_agent_gate_result", "source": "trace_envelope"}, {"event_id": 305434, "event_kind": "dual_agent_gate_result", "source": "payload"}] |
| FM-2.1 | Inter-Agent Misalignment | Conversation reset | not_observed_in_run | covered_by_deterministic_probe | payload | classify_failure | blocked payload reason `conversation_reset` | [] |
| FM-2.2 | Inter-Agent Misalignment | Fail to ask for clarification | not_observed_in_run | covered_by_deterministic_probe | payload | classify_failure | blocked payload reason `ambiguous_input_no_clarification` | [] |
| FM-2.3 | Inter-Agent Misalignment | Task derailment | not_observed_in_run | covered_by_deterministic_probe | payload | classify_failure | blocked payload reason `scope_violation_off_scope` | [] |
| FM-2.4 | Inter-Agent Misalignment | Information withholding | not_observed_in_run | covered_by_deterministic_probe | payload | failure_taxonomy_for_payload | Cursor or reviewer disagreement probe fails | [] |
| FM-2.5 | Inter-Agent Misalignment | Ignored other agent input | not_observed_in_run | covered_by_deterministic_probe | sequence | detect_sequence_failures | Claude response does not address a prior Codex objection | [] |
| FM-2.6 | Inter-Agent Misalignment | Reasoning-action mismatch | not_observed_in_run | covered_by_deterministic_probe | payload | failure_taxonomy_for_payload | outcome fidelity or message protocol failure | [] |
| FM-3.1 | Task Verification | Premature termination | not_observed_in_run | covered_by_deterministic_probe | payload+sequence | failure_taxonomy_for_payload + detect_sequence_failures | accepted gate is missing a required probe | [] |
| FM-3.2 | Task Verification | No or incomplete verification | not_observed_in_run | covered_by_deterministic_probe | payload | failure_taxonomy_for_payload | claim verification or evidence receipt probe fails | [] |
| FM-3.3 | Task Verification | Incorrect verification | observed_in_run | covered_by_deterministic_probe | sequence | detect_sequence_failures | Cursor rejects after the gate was accepted | [{"details": {"accepted_gate_event_id": 305428}, "event_ids": [305428, 305431], "source": "sequence"}] |
