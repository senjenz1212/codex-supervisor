# Planning Artifact Validator PRD

## Problem Statement

The dual-agent workflow now owns lifecycle order, state, transcript export,
visual evidence, and optional Cursor review, but it can still advance on
artifact-shaped source documents. A generated `prd.md` with only an intent
sentence, an `issues.md` that says future runs should create issues, or a TDD
file with no public-boundary tests can satisfy presence checks and checksums.
That lets Codex, Claude, or Cursor appear to follow PRD-to-TDD while the
supervisor has no proof that real planning happened.

## Solution

Add a deterministic planning-quality validator at the supervisor acceptance
boundary. The validator reads the explicit planning artifact paths passed in the
handoff packet, applies LLM-free substance checks per artifact kind, records a
`dual_agent_planning_validation` receipt in the SQLite ledger, and blocks
lifecycle-critical gates before Claude `/lead` is invoked when artifacts are
missing, stub-like, internally open, or cross-artifact traceability does not
resolve.

## User Stories

1. As Sam, I want the workflow to block on stub PRDs, so that a green run never hides placeholder planning.
2. As Sam, I want `issues.md` to contain implementation slices, so that agents cannot replace issue slicing with future-tense notes.
3. As Sam, I want TDD plans to name public-boundary RED tests, so that implementation cannot start from helper-only proof.
4. As Sam, I want grill findings to be resolved or explicitly waived in the document, so that known objections cannot be skipped silently.
5. As Sam, I want implementation plans to reference real PRD promises and TDD tests, so that plan review proves translation continuity.
6. As Sam, I want planning-validation receipts in the ledger, so that later chat sessions and Cortex can explain why a gate blocked.
7. As Sam, I want `artifact_policy="relaxed"` to remain unable to bypass planning substance, so that convenience flags cannot become a hack path.
8. As Sam, I want the validator to be deterministic, so that replay fixtures reproduce gate decisions without another LLM judgment.

## PRD Promise Contracts

P1. Stub artifacts block before worker execution
User-visible promise: If a lifecycle gate receives generated, placeholder, TBD, or one-line planning artifacts, the gate returns `blocked` before invoking Claude `/lead`.
Representative prompts or actions: Start `run_dual_agent_workflow` on auto-seeded source docs; call `start_dual_agent_gate(gate="execution")` with placeholder `prd.md`.
Public boundary: `dual_agent_runner`
Allowed outcomes: `DualAgentGateResult(status="blocked")` with a red planning probe and no worker invocation; ledger receipt explaining failed checks when state is supplied.
Forbidden outcomes: Claude runs on stub artifacts; `artifact_policy="relaxed"` bypasses substance checks; the gate reports accepted from section headers only.
Related user stories: 1, 2, 7, 8

P2. Artifact kind rules are substantive and deterministic
User-visible promise: PRD, issues, TDD, grill findings, and implementation plan artifacts must pass deterministic structure and substance checks, including anti-template and blocked-phrase detection.
Representative prompts or actions: Validate good, stub, and sneaky fixtures for each artifact kind.
Public boundary: `dual_agent_planning_validator`
Allowed outcomes: Good fixtures pass; stub and sneaky fixtures fail with stable check IDs; no validation check calls an LLM or live model.
Forbidden outcomes: A file with required headings and `TBD` bodies passes; validator output changes because a model was invoked; a missing artifact kind is treated as accepted.
Related user stories: 1, 2, 3, 4, 8

P3. Cross-artifact traceability resolves
User-visible promise: Implementation plans must include a traceability block whose referenced PRD promise IDs and TDD test names actually exist in the source PRD and TDD artifacts.
Representative prompts or actions: Validate an implementation plan that references `P9` or `test_fake_name` when the PRD/TDD define neither.
Public boundary: `dual_agent_planning_validator`
Allowed outcomes: The validator blocks unresolved traceability and reports the missing references by check ID.
Forbidden outcomes: A fabricated traceability block passes because it has the right heading; implementation can start after plan review with references to nonexistent PRD promises or tests.
Related user stories: 3, 5, 8

P4. Receipts are durable and inspectable
User-visible promise: Every planning validation writes a ledger receipt with validator version, artifact hashes, check verdicts, and the final verdict whenever a state handle is available.
Representative prompts or actions: Run `start_dual_agent_gate` with a failing PRD, then ask `read_gate_transcript` for the task.
Public boundary: `codex_supervisor_mcp`
Allowed outcomes: The transcript includes planning validation receipts; blocked gate results point to the same failed check set.
Forbidden outcomes: Planning validation only appears in process memory; later chats cannot reconstruct why a gate was blocked; receipt payloads omit failed check IDs.
Related user stories: 6, 8

## Implementation Decisions

- Build a new deep module named `supervisor.planning_validator` with a small interface: validate explicit planning artifact paths and return typed check results.
- Keep all checks LLM-free: regex, section parsing, count thresholds, hash comparison, and deterministic cross-reference resolution only.
- Reuse the existing `PlanningArtifact` and handoff artifact path model. Do not introduce a second path convention.
- Wire validation into `run_dual_agent_gate` after lock acquisition but before `write_handoff_packet` and before `/lead` invocation.
- Include a green planning probe in accepted gates and a red planning probe in blocked gates.
- Extend the transcript/event surfaces to include `dual_agent_planning_validation` receipts.
- Defer supervisor-level operator planning waivers to a later slice. Artifact-internal grill waivers remain data in `grill-findings.md` and require reasons.

## Testing Decisions

- The first RED tests target public boundaries: `dual_agent_runner`, `codex_supervisor_mcp`, and `dual_agent_planning_validator`.
- Fixture coverage must include `good.md`, `stub.md`, and `sneaky.md` for PRD, issues, TDD, grill findings, and implementation plan.
- Tests must prove worker invocation is skipped on failed planning validation by counting fake-runner calls.
- Tests must prove `artifact_policy="relaxed"` still blocks when planning substance fails.
- Tests must prove `read_gate_transcript` can surface planning-validation receipts.
- Existing lifecycle tests that expect accepted workflows must pre-create substantive source artifacts instead of relying on auto-seeded stubs.

## Out of Scope

- Operator-level planning waivers and expiry.
- LLM-based document quality scoring.
- Enforcing that Matt Pocock skills literally ran as processes; this slice enforces the durable artifacts those skills must produce.
- Live Claude, Cursor, Telegram, or Browser probes.

## Further Notes

This PRD intentionally treats the supervisor ledger as the truth surface. The
agent conversation can propose, critique, and revise, but the code path that
starts `/lead` must be unable to proceed on placeholder source documents.
