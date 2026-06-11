# PRD: Defense-In-Depth Hardening Around Auto-Evolution

## Problem Statement

The auto-evolution loop can draft experiments and policy proposals, but its secondary checks still have brittle edges. Planning validation is deterministic but mostly structural, so keyword-stuffed PRD or TDD text can satisfy static checks while saying little. The no-mistakes adapter accepts legacy stdout markers and JSON snippets without a strict contract, so malformed output can look like an empty advisory pass. Fan-out evidence-grade defaults still allow weaker receipts unless callers opt into stronger floors. Dynamic workflow receipt hash mismatches are detected, but the probe reason can be reported as a missing receipt rather than a clear tamper red signal. Phase D hardens these defense-in-depth layers without changing primary gate authority, fan-out defaults, or the human approval model.

## Solution

Add a planning semantic rubric that runs after the existing regex validator and before the lead worker. It produces a replayable rubric score from local deterministic substance checks and, when configured, can also carry a Decision Runtime reviewer output. The planning gate accepts only when the regex checks pass and the semantic score meets the configured threshold; unavailable rubric evaluation follows the planning-rubric unavailable policy and never silently passes.

Tighten the no-mistakes integration by defining a structured JSON contract with schema version, outcome, gate, and findings. Valid JSON findings are parsed deterministically. Malformed JSON is an advisory finding by default and a hard block when policy is required. The adapter stays post-acceptance and external; it does not become gate authority.

Raise the effective evidence-grade floor at execution-oriented agentic checks without altering `agentic_lead.policy`: when fan-out receipts are present at execution or outcome review, worker receipts must satisfy runtime-native provenance; other fan-out contexts require at least lead-captured provenance. Finally, make dynamic receipt sha256 mismatch a distinct red probe reason so tampered transcript or output hashes are impossible to record as an accepted downgrade.

## User Stories

- As an operator, I want shallow PRD or TDD artifacts to be blocked even when they contain the required headings, because an auto-evolution loop should not learn from hollow plans.
- As a reviewer, I want planning rubric scores and reasons written into the ledger so I can replay why a gate was blocked or accepted.
- As a maintainer, I want no-mistakes output to be a deterministic JSON contract so external validation cannot pass by emitting malformed or ambiguous stdout.
- As a gate owner, I want fan-out worker receipts at execution gates to meet supervisor-owned runtime-native evidence when fan-out evidence is present.
- As an incident investigator, I want transcript or artifact hash tampering to be reported as a red tamper probe, not hidden inside a generic missing-receipt failure.

## PRD Promise Contracts

P1. Planning semantic rubric blocks hollow artifacts.

- User-visible promise: PRD/TDD planning gates require deterministic regex checks and a substance score at or above threshold.
- Public boundary: `validate_planning_artifacts` and `planning_validation_probe`.
- Allowed outcomes: a rubric score, threshold, reasons, and replayable artifact hashes appear in the planning validation payload.
- Forbidden outcomes: keyword-stuffed artifacts with required headings but no concrete substance pass planning gates silently.

P2. Rubric unavailable never silently passes.

- User-visible promise: If the rubric runtime is unavailable, the decision follows the configured planning-rubric unavailable policy and records the failure.
- Public boundary: `validate_planning_artifacts` and dual-agent planning event payloads.
- Allowed outcomes: block/proceed-degraded behavior is explicit and replayable.
- Forbidden outcomes: missing rubric output is treated as acceptance without an event.

P3. no-mistakes uses a structured contract.

- User-visible promise: no-mistakes findings are parsed from a stable JSON schema and malformed JSON is surfaced as a finding.
- Public boundary: `parse_no_mistakes_output` and `run_no_mistakes_validation`.
- Allowed outcomes: deterministic findings with id, severity, file, line, description, and action fields; malformed output is advisory under advisory policy and blocking under required policy.
- Forbidden outcomes: malformed JSON or legacy-looking noise is ignored and reported as checks-passed.

P4. Evidence-grade defaults are raised for fan-out execution evidence.

- User-visible promise: When fan-out receipts are present at execution or outcome review, runtime-native evidence is required without changing whether fan-out itself is enabled.
- Public boundary: `_agentic_lead_policy_config` and `verify_dynamic_workflow_receipts`.
- Allowed outcomes: fan-out-free runs are unaffected; fan-out receipts at execution gates must be supervisor-owned runtime-native; other fan-out contexts require at least lead-captured evidence.
- Forbidden outcomes: self-reported fan-out worker evidence satisfies execution or outcome-review floors.

P5. Tampered dynamic receipt hashes become red tamper probes.

- User-visible promise: Any sha256 mismatch for dynamic workflow transcript, output, manifest, replay, or comparison refs yields an explicit red probe.
- Public boundary: `verify_dynamic_workflow_receipts`.
- Allowed outcomes: probe reason such as `dynamic_workflow_receipt_hash_mismatch` with invalid gate details.
- Forbidden outcomes: a tampered hash is hidden as missing receipts or recorded as accepted with downgraded confidence.

## Implementation Decisions

- Keep the existing deterministic planning validator as the first floor and add rubric results to the same `PlanningValidationResult` payload so the ledger remains the source of truth.
- Implement the first rubric pass as deterministic substance scoring, with fields designed to accept future Decision Runtime model output without making this slice depend on a live model for unit tests.
- Add config defaults for planning rubric threshold and unavailable policy; allow the threshold to be overlayable only within conservative bounds already controlled by Phase B, including a nonzero floor so operator overlays cannot disable the semantic planning gate.
- Preserve `agentic_lead.policy` and fan-out concurrency defaults. Only compute a stronger effective evidence grade when fan-out receipts exist and the current gate is execution oriented.
- Replace no-mistakes stdout-marker dependence with a structured JSON parser while retaining legacy parsing as a compatibility fallback behind an explicit contract status.
- Treat malformed structured JSON as a finding with `finding_id=no-mistakes-malformed-json` so required policy blocks and advisory policy records evidence without becoming primary authority.
- Add a dedicated dynamic receipt tamper reason when any replay/ref validation problem contains `sha256_mismatch` or `output_hash_mismatch`.

## Testing Decisions

- Use public-boundary tests against `validate_planning_artifacts` and `planning_validation_probe` for rubric blocking, not only helper functions.
- Keep fixture tests for the deterministic regex validator green and add hollow-but-keyword-rich fixtures to prove the semantic rubric blocks real planning-gate inputs.
- Test no-mistakes structured JSON parsing directly and through `run_no_mistakes_validation` under advisory and required policies.
- Test fan-out-free lead-direct runs remain unaffected while execution-gate fan-out receipts with only lead-captured evidence fail the effective runtime-native floor.
- Test tampered transcript/output hash returns a red tamper reason and never a green probe.

## Out Of Scope

- No change to the primary gate sequence or typed outcome algebra.
- No change to fan-out/concurrency defaults.
- No automatic policy mutation.
- No runtime adoption of a new external verifier.
- No removal of the existing deterministic regex planning layer.
