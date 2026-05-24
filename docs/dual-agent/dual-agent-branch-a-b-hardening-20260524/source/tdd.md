# Dual-Agent Branch A+B Hardening TDD Plan

## Public Boundary

The first RED tests hit supervisor public boundaries: `State.write_event`, `run_dual_agent_workflow`, `read_gate_transcript`, and exported interaction artifacts. Helper-only taxonomy tests can follow, but acceptance depends on the ledger and workflow driver behavior.

## Test Cases

### test_trace_envelope_stamps_dual_agent_payloads_without_wrapping_original_shape

Maps to: ISS-1, P1, P2
RED: A blocked dual-agent payload lacks a `trace_envelope` and failure taxonomy.
GREEN: `stamp_trace_envelope` appends policy verdict and taxonomy while preserving original fields.

### test_blocking_red_probe_beats_accepted_completion

Maps to: ISS-1, P1
RED: P11/P12/P_planning red probes are ignored by snapshot liveness.
GREEN: central blocking-probe set treats them as validation blockers while still ignoring P4 pause signals.

### test_run_dual_agent_workflow_requires_prd_tdd_skill_receipts

Maps to: ISS-2, P3
RED: Workflow advances to PRD review with no skill provenance.
GREEN: Workflow blocks at `workflow_start` with `P12 missing_prd_tdd_skill_receipts`.

### test_read_gate_transcript_includes_skill_receipt_validation

Maps to: ISS-2, P3
RED: Skill receipt validation is written but not visible through transcript reads.
GREEN: Transcript includes `skill_receipt_validations`.

### test_codex_confidence_report_and_review_packet_explain_decision

Maps to: ISS-3, P4
RED: Codex confidence is an unexplained scalar.
GREEN: `codex_review_packet` records requirements, evidence refs, confidence criteria, and objections.

### test_probe_cursor_sdk_live_writes_skipped_fixture_without_key

Maps to: ISS-4, P5
RED: Cursor live probe has no durable diagnostic when credentials are absent.
GREEN: The script writes a skipped fixture without exposing or requiring the API key.

## RED/GREEN Plan

1. Add failing taxonomy and trace envelope tests.
2. Add the pure taxonomy/envelope helpers and stamp dual-agent events at the ledger boundary.
3. Add failing skill receipt workflow tests.
4. Add `P12` skill receipt verification and transcript visibility.
5. Add Codex review packet tests and wire the packet into gate decision messages.
6. Add the Cursor live probe script and run it once in the current environment to capture the honest credential status.
