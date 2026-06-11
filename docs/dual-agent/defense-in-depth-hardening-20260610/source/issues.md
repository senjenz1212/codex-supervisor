# Issues: Defense-In-Depth Hardening

## Slice D1: Planning Semantic Rubric

Priority: P1

Scope: Extend `supervisor/planning_validator.py` with a deterministic semantic rubric result, config threshold plumbing, ledger payload fields, and dual-agent runner wiring. The rubric scores PRD and TDD substance, concrete public boundaries, promise/test traceability, and anti-stub evidence.

Acceptance criteria:

- [ ] Hollow PRD/TDD artifacts with required headings fail planning validation when the score is below threshold.
- [ ] Rubric score, threshold, reasons, artifact hashes, and policy decision are emitted in `dual_agent_planning_validation` payloads.
- [ ] Config, policy-overlay, and explicit gate thresholds below the conservative minimum are clamped and cannot disable the semantic floor.
- [ ] Existing good fixture artifacts still pass.
- [ ] Rubric unavailable behavior is explicit and never silent.

PRD promise: P1, P2.

Public boundary for first RED test: `validate_planning_artifacts`.

## Slice D2: no-mistakes Structured Contract

Priority: P1

Scope: Add a strict JSON output contract to `supervisor/no_mistakes.py`, parse structured findings deterministically, and report malformed structured output as a finding. Required policy blocks; advisory records the finding without primary gate authority.

Acceptance criteria:

- [ ] Valid JSON contract output creates deterministic findings and outcome/gate fields.
- [ ] Malformed JSON output produces a `no-mistakes-malformed-json` finding.
- [ ] Required policy blocks on malformed output; advisory policy records evidence without claiming checks passed.
- [ ] Legacy plain-text parsing remains compatibility fallback for non-JSON output.

PRD promise: P3.

Public boundary for first RED test: `run_no_mistakes_validation`.

## Slice D3: Effective Evidence-Grade Defaults

Priority: P1

Scope: Update the agentic policy/evidence-grade boundary so fan-out receipts at execution and outcome_review require runtime-native supervisor provenance, while other fan-out contexts require lead-captured minimum. Do not alter configured `agentic_lead.policy`.

Acceptance criteria:

- [ ] Fan-out-free runs keep existing behavior.
- [ ] Execution/outcome fan-out receipts with only self-reported or lead-captured evidence fail the effective runtime-native floor.
- [ ] Non-execution fan-out receipts require lead-captured minimum.
- [ ] Configured explicit runtime_native still works as before.

PRD promise: P4.

Public boundary for first RED test: `verify_dynamic_workflow_receipts`.

## Slice D4: Dynamic Receipt Tamper Probe

Priority: P2

Scope: Make sha256 and output-hash mismatches in dynamic workflow receipts return an explicit red tamper reason with invalid gate details.

Acceptance criteria:

- [ ] Tampered transcript/output/replay refs return `dynamic_workflow_receipt_hash_mismatch`.
- [ ] The probe details preserve invalid gate and verified-ref context.
- [ ] Non-tamper missing receipts continue to use the existing missing/invalid receipt reasons.

PRD promise: P5.

Public boundary for first RED test: `verify_dynamic_workflow_receipts`.
