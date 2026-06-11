# Implementation Plan: Defense-In-Depth Hardening

## Files / Modules To Touch

- `supervisor/planning_validator.py`
- `supervisor/dual_agent_runner.py`
- `supervisor/config.py`
- `supervisor/no_mistakes.py`
- `supervisor/dynamic_workflow_receipts.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `tests/test_planning_validator.py`
- `tests/test_no_mistakes.py`
- `tests/test_dynamic_workflow_receipts.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_dual_agent_runner.py`
- `tests/test_target_config_load.py`
- `tests/test_policy_overlay.py`
- `docs/dual-agent/defense-in-depth-hardening-20260610/`

## Steps

1. Extend planning validation with a rubric result that scores concrete promises, public-boundary references, test names, non-stub density, and traceability. Add rubric fields to the planning event payload, keep existing regex checks as mandatory, and clamp direct `validate_planning_artifacts` caller thresholds to the same nonzero floor.
2. Add config defaults for planning rubric threshold and unavailable policy. Clamp threshold values to conservative bounds when loaded from overlay/config, including a nonzero minimum so an overlay cannot disable the semantic gate, and wire the live `dual_agent_runner.py` planning-validation callsite so the configured rubric policy is present in `dual_agent_planning_validation` events.
3. Replace no-mistakes stdout-marker-first parsing with a structured JSON contract parser, keeping legacy text parsing as fallback. Malformed structured JSON creates a deterministic finding.
4. Adjust no-mistakes result semantics so malformed structured output blocks under required and records advisory evidence under advisory, while a valid passing structured outcome with a contextual `gate` and no findings remains accepted.
5. Compute an effective evidence-grade floor in the agentic policy boundary when fan-out receipts exist, using runtime-native for execution/outcome_review and lead-captured for other fan-out gates while leaving `agentic_lead.policy` unchanged. Update dynamic-preview workflow-driver fixtures so accepted fan-out paths use supervisor-owned runtime-native refs without creating spurious discovered workers.
6. Add an explicit tamper probe reason for dynamic receipt sha256/output-hash mismatches.
7. Run the focused tests for planning validation, no-mistakes, dynamic workflow receipts, and runner evidence-grade/rubric plumbing, then run the broader AutoResearch/supervisor regression subset.

## Risks

- A semantic rubric that is too strict could block legitimate concise PRDs. Mitigation: use a default threshold of 0.6, expose reasons, and validate against existing good fixtures.
- Evidence-grade default hardening could unintentionally affect fan-out-free lead-direct runs. Mitigation: compute the stronger floor only when fan-out worker receipts are present.
- no-mistakes malformed JSON handling could turn advisory mode into a primary gate. Mitigation: required blocks, advisory records evidence but remains secondary.
- A generic hash mismatch reason could obscure missing-file cases. Mitigation: emit the tamper reason only for mismatch strings, not ordinary missing refs.

## Traceability

- P1 is covered by `test_planning_validator_blocks_keyword_stuffed_prd_with_low_semantic_score`, `test_keyword_stuffed_prd_blocks_at_default_threshold_0_6`, `test_validate_planning_artifacts_threshold_zero_is_clamped_at_public_boundary`, `test_planning_rubric_payload_is_replayable_for_good_artifacts`, `test_dual_agent_runner_records_planning_rubric_config_in_validation_event`, `test_planning_rubric_threshold_cannot_be_configured_to_zero`, `test_policy_overlay_rubric_threshold_cannot_disable_planning_floor`, and `test_explicit_gate_rubric_threshold_cannot_disable_planning_floor`.
- P2 is covered by `test_planning_rubric_unavailable_follows_policy_and_never_silently_passes`, `test_validate_planning_artifacts_threshold_zero_is_clamped_at_public_boundary`, `test_dual_agent_runner_records_planning_rubric_config_in_validation_event`, `test_planning_rubric_threshold_cannot_be_configured_to_zero`, `test_policy_overlay_rubric_threshold_cannot_disable_planning_floor`, and `test_explicit_gate_rubric_threshold_cannot_disable_planning_floor`.
- P3 is covered by `test_no_mistakes_adapter_parses_structured_json_contract`, `test_no_mistakes_structured_checks_passed_with_gate_accepts`, `test_no_mistakes_required_blocks_malformed_structured_json`, and `test_no_mistakes_advisory_records_malformed_structured_json_as_secondary_evidence`.
- P4 is covered by `test_execution_fanout_requires_runtime_native_evidence_by_default`, `test_outcome_review_fanout_requires_runtime_native_evidence_by_default`, `test_non_execution_fanout_requires_lead_captured_evidence_by_default`, and `test_run_dual_agent_workflow_generates_dynamic_manifest_and_auto_receipts`.
- P5 is covered by `test_dynamic_receipt_hash_mismatch_returns_tamper_reason`.
- D2-AC4 legacy plain-text fallback is preserved by retaining `test_no_mistakes_adapter_parses_outcome_and_gate_findings` while adding the structured JSON tests.
- D3-AC4 explicit `runtime_native` passthrough is preserved by retaining `test_agentic_required_accepts_supervisor_owned_runtime_native_receipts` while raising the default effective floor.
