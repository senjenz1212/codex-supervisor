# Dual-Agent MAST Coverage Matrix

This matrix is the human-facing companion to generated `mast-coverage.md` and
`replay/mast-coverage.json` artifacts. It lists every deterministic
MAST-inspired mode the supervisor can classify, the trigger surface, and the
test seam that proves it.

| MAST code | Category | Mode | Trigger surface | Entrypoint | Proof |
|---|---|---|---|---|---|
| `FM-1.1` | Specification Issues | Disobey task specification | Payload | `failure_taxonomy_for_payload` | `test_failure_taxonomy_triggers_all_mast_modes_through_payload_or_sequence_rules` |
| `FM-1.2` | Specification Issues | Disobey role specification | Payload | `failure_taxonomy_for_payload -> classify_failure` | `test_mast_coverage_matrix_reports_every_mode_and_sequence_sources` |
| `FM-1.3` | Specification Issues | Step repetition | Sequence | `detect_sequence_failures` | `test_export_dual_agent_run_artifacts_writes_sequence_failure_diagnostics` |
| `FM-1.4` | Specification Issues | Loss of conversation history | Payload | `classify_failure` | `test_failure_taxonomy_triggers_all_mast_modes_through_payload_or_sequence_rules` |
| `FM-1.5` | Specification Issues | Unaware of termination conditions | Payload | `classify_failure` | `test_failure_taxonomy_triggers_all_mast_modes_through_payload_or_sequence_rules` |
| `FM-2.1` | Inter-Agent Misalignment | Conversation reset | Payload | `classify_failure` | `test_failure_taxonomy_triggers_all_mast_modes_through_payload_or_sequence_rules` |
| `FM-2.2` | Inter-Agent Misalignment | Fail to ask for clarification | Payload | `classify_failure` | `test_failure_taxonomy_triggers_all_mast_modes_through_payload_or_sequence_rules` |
| `FM-2.3` | Inter-Agent Misalignment | Task derailment | Payload | `classify_failure` | `test_failure_taxonomy_triggers_all_mast_modes_through_payload_or_sequence_rules` |
| `FM-2.4` | Inter-Agent Misalignment | Information withholding | Payload | `failure_taxonomy_for_payload` | `test_failure_taxonomy_triggers_all_mast_modes_through_payload_or_sequence_rules` |
| `FM-2.5` | Inter-Agent Misalignment | Ignored other agent input | Sequence | `detect_sequence_failures` | `test_export_dual_agent_run_artifacts_writes_sequence_failure_diagnostics` |
| `FM-2.6` | Inter-Agent Misalignment | Reasoning-action mismatch | Payload | `failure_taxonomy_for_payload` | `test_failure_taxonomy_triggers_all_mast_modes_through_payload_or_sequence_rules` |
| `FM-3.1` | Task Verification | Premature termination | Payload and sequence | `failure_taxonomy_for_payload + detect_sequence_failures` | `test_trace_envelope_classifies_accepted_payload_with_missing_required_probe` |
| `FM-3.2` | Task Verification | No or incomplete verification | Payload | `failure_taxonomy_for_payload` | `test_trace_envelope_stamps_dual_agent_payloads_without_wrapping_original_shape` |
| `FM-3.3` | Task Verification | Incorrect verification | Sequence | `detect_sequence_failures` | `test_export_dual_agent_run_artifacts_writes_sequence_failure_diagnostics` |

Generated run artifacts add run-specific coverage:

- `mast-coverage.md` is the human matrix for a single exported run.
- `replay/mast-coverage.json` is the machine-readable equivalent.
- `replay/manifest.json` embeds the same matrix under `mast_coverage` and
  includes sequence failures under `sequence_failures`.
