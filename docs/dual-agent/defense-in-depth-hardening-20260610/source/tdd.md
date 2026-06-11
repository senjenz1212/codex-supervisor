# TDD Plan: Defense-In-Depth Hardening

## test_planning_validator_blocks_keyword_stuffed_prd_with_low_semantic_score

Maps to: Slice D1, P1/P2.

Red: A PRD containing the required headings and repeated gate keywords but no concrete promises, boundaries, or tests passes the existing structural validator.

Green: `validate_planning_artifacts` returns blocked with rubric score below threshold, includes the rubric reasons in the planning payload, and `planning_validation_probe` reports red.

## test_keyword_stuffed_prd_blocks_at_default_threshold_0_6

Maps to: Slice D1, P1.

Red: A hollow PRD only proves failure when the caller manually raises the rubric threshold above the production default.

Green: The default `validate_planning_artifacts` public boundary blocks a keyword-stuffed PRD at the configured `0.6` threshold with deterministic planning failures.

## test_validate_planning_artifacts_threshold_zero_is_clamped_at_public_boundary

Maps to: Slice D1, P1/P2.

Red: A direct caller can pass `rubric_threshold=0.0` into `validate_planning_artifacts`, bypassing the config/API clamps and disabling `RUBRIC-001`.

Green: The validator clamps direct caller thresholds to `PLANNING_RUBRIC_MIN_THRESHOLD`, and a low-scoring rubric result still blocks.

## test_planning_rubric_payload_is_replayable_for_good_artifacts

Maps to: Slice D1, P1.

Red: Accepted planning artifacts do not record threshold, score, version, reasons, or artifact hashes, making rubric decisions unreplayable.

Green: Good planning fixtures pass and the event payload contains stable rubric fields plus artifact hashes.

## test_planning_rubric_unavailable_follows_policy_and_never_silently_passes

Maps to: Slice D1, P2.

Red: The semantic rubric runtime can be unavailable or return no rubric output, yet `validate_planning_artifacts` still reports an accepted planning result without recording the missing rubric decision.

Green: `validate_planning_artifacts` records rubric unavailable state, applies the configured unavailable policy, and never treats missing rubric output as silent acceptance; block policy yields a red `planning_validation_probe`, while proceed-degraded behavior is explicit and replayable.

## test_dual_agent_runner_records_planning_rubric_config_in_validation_event

Maps to: Slice D1, P1/P2.

Red: The live dual-agent runner calls `validate_planning_artifacts` without the configured rubric threshold/unavailable policy and writes a `dual_agent_planning_validation` event that omits the rubric policy fields, so replay cannot prove which configured floor was enforced.

Green: The live runner path threads or loads the configured rubric threshold/unavailable policy, records the rubric fields in the `dual_agent_planning_validation` payload, and keeps existing good planning artifacts accepted.

## test_planning_rubric_threshold_cannot_be_configured_to_zero

Maps to: Slice D1, P1/P2.

Red: A config file can set `planning_rubric.threshold: 0.0`, silently neutralizing the semantic planning gate while still emitting apparently valid rubric payloads.

Green: Config-loaded rubric thresholds below the conservative minimum are clamped to `PLANNING_RUBRIC_MIN_THRESHOLD`, keeping the planning floor non-disabling and replayable.

## test_policy_overlay_rubric_threshold_cannot_disable_planning_floor

Maps to: Slice D1, P1/P2.

Red: A policy overlay can set a gate-specific rubric threshold of `0.0`, bypassing the config clamp and weakening future planning validation.

Green: Overlay-provided rubric thresholds are clamped through the same nonzero conservative minimum before being passed into `DualAgentGateSpec`.

## test_explicit_gate_rubric_threshold_cannot_disable_planning_floor

Maps to: Slice D1, P1/P2.

Red: A direct `start_dual_agent_gate` caller can pass `planning_rubric_threshold=0.0`, bypassing the config and overlay clamps.

Green: Explicit gate arguments are clamped at the API gate-spec construction boundary before the runner sees them.

## test_no_mistakes_adapter_parses_structured_json_contract

Maps to: Slice D2, P3.

Red: no-mistakes JSON output is parsed only by opportunistic stdout markers and loses structured finding fields.

Green: `parse_no_mistakes_output` reads the structured JSON contract and returns deterministic outcome, gate, and finding objects.

## test_no_mistakes_structured_checks_passed_with_gate_accepts

Maps to: Slice D2, P3.

Red: A valid structured JSON report with `outcome="checks_passed"`, a contextual `gate`, and no findings is blocked because the adapter treats any truthy `gate` value as a failure.

Green: `run_no_mistakes_validation` treats `gate` as context when there are no findings and the outcome is passing, returning `accepted` with `no_mistakes_checks_passed`.

## test_no_mistakes_adapter_parses_outcome_and_gate_findings

Maps to: Slice D2, D2-AC4.

Red: Replacing the stdout-marker parser with structured JSON removes support for legacy plain-text no-mistakes findings and gate markers.

Green: The legacy fallback still parses outcome, gate, and findings when no structured JSON contract is present.

## test_no_mistakes_required_blocks_malformed_structured_json

Maps to: Slice D2, P3.

Red: Malformed JSON output from no-mistakes is ignored, so required policy can report checks-passed or a vague failure without a structured finding.

Green: `run_no_mistakes_validation` emits a malformed-json finding and returns `required_blocked` when policy is required.

## test_no_mistakes_advisory_records_malformed_structured_json_as_secondary_evidence

Maps to: Slice D2, P3.

Red: Advisory no-mistakes either silently passes malformed output or blocks the primary workflow.

Green: Advisory policy returns an advisory blocked/skipped evidence result with the malformed-json finding recorded and no primary authority escalation.

Regression guard: retain or extend the existing legacy plain-text fallback test so D2-AC4 remains covered while the JSON contract becomes preferred.

## test_execution_fanout_requires_runtime_native_evidence_by_default

Maps to: Slice D3, P4.

Red: Execution-gate fan-out receipts with lead-captured artifacts satisfy the default configured evidence grade.

Green: `verify_dynamic_workflow_receipts` blocks execution/outcome fan-out receipts unless their refs are supervisor-owned runtime-native.

## test_outcome_review_fanout_requires_runtime_native_evidence_by_default

Maps to: Slice D3, P4.

Red: Outcome-review fan-out receipts with only lead-captured artifacts satisfy the default configured evidence grade, leaving the final gate weaker than execution.

Green: `verify_dynamic_workflow_receipts` applies the same runtime-native fan-out floor to `outcome_review` and blocks lead-captured-only fan-out evidence.

## test_agentic_required_accepts_supervisor_owned_runtime_native_receipts

Maps to: Slice D3, D3-AC4.

Red: Raising the effective fan-out evidence floor accidentally rejects already-valid supervisor-owned `runtime_native` receipts.

Green: Existing supervisor-owned `runtime_native` fan-out receipts still satisfy required agentic evidence while weaker default receipts are blocked.

## test_non_execution_fanout_requires_lead_captured_evidence_by_default

Maps to: Slice D3, P4.

Red: Non-execution fan-out receipts can satisfy the floor with self-reported evidence.

Green: Non-execution fan-out receipts require at least lead-captured evidence while fan-out-free runs remain unaffected.

Regression guard: retain or extend the existing explicit `runtime_native` acceptance test so D3-AC4 remains covered while the effective default floor is raised.

## test_run_dual_agent_workflow_generates_dynamic_manifest_and_auto_receipts

Maps to: Slice D3, P4.

Red: Dynamic-preview fan-out fixtures either provide lead-captured-only worker refs or accidentally create discoverable agentic-worker receipts with missing budget metadata, causing a workflow-start or execution gate block.

Green: Accepted dynamic-preview fan-out receipts use supervisor-owned runtime-native refs outside the agentic-worker discovery lane, preserving manifest synthesis and allowing the workflow to advance.

## test_dynamic_receipt_hash_mismatch_returns_tamper_reason

Maps to: Slice D4, P5.

Red: Tampering with a transcript after receipt creation returns `missing_dynamic_workflow_receipts`, which obscures the sha256 mismatch.

Green: The same tamper returns a red `dynamic_workflow_receipt_hash_mismatch` probe with invalid gate details and no acceptance.
