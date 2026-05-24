# Planning Artifact Validator Issues

## Slice ISS-1: Deterministic Artifact Substance Validator

Type: AFK
Priority: P0
Estimate: M
Scope: Add a pure `supervisor.planning_validator` module that validates explicit
planning artifact paths for PRD, issues, TDD plan, grill findings, and
implementation plan kinds.
PRD promise: P1, P2, P3
First public-boundary RED test: `dual_agent_planning_validator` validates
`tests/fixtures/planning_validator/<kind>/{good,stub,sneaky}.md`.

Acceptance Criteria:
- [ ] Good fixtures for every artifact kind pass with stable check IDs.
- [ ] Stub and sneaky fixtures fail deterministically without LLM calls.
- [ ] Implementation-plan traceability references resolve against real PRD
      promise IDs and TDD test names.

## Slice ISS-2: Runner-Level Planning Gate

Type: AFK
Priority: P0
Estimate: M
Scope: Wire planning validation into `run_dual_agent_gate` before handoff packet
writing and Claude `/lead` invocation, while preserving lock behavior and
existing P1/P2/P3 probes.
PRD promise: P1, P4
First public-boundary RED test: `dual_agent_runner` blocks a stub PRD at
`prd_review` with zero fake-runner calls.

Acceptance Criteria:
- [ ] Failed planning validation returns `status="blocked"` and `attempts=0`.
- [ ] Accepted gates include a green planning probe.
- [ ] Existing lock-held behavior still returns the lock probe before planning
      validation tries to run.

## Slice ISS-3: MCP Receipt and Transcript Exposure

Type: AFK
Priority: P1
Estimate: S
Scope: Persist `dual_agent_planning_validation` receipts and surface them via
the Codex-facing transcript tool.
PRD promise: P4
First public-boundary RED test: `codex_supervisor_mcp` calls
`start_dual_agent_gate` with a sneaky PRD, then `read_gate_transcript` and sees
the failed receipt with check IDs.

Acceptance Criteria:
- [ ] Receipt payload includes validator version, artifact hashes, checks, and
      final verdict.
- [ ] `read_gate_transcript` returns planning validation receipts ordered with
      the rest of the gate events.
- [ ] Receipt data is redacted through the existing state writer.

## Slice ISS-4: Workflow Regression Against Auto-Seeded Stubs

Type: AFK
Priority: P1
Estimate: S
Scope: Update workflow tests so accepted runs pre-create substantive artifacts
and add a regression that auto-seeded source docs block before worker execution.
PRD promise: P1, P2
First public-boundary RED test: `codex_supervisor_mcp.run_dual_agent_workflow`
on a fresh task blocks at `prd_review` when only generated source docs exist.

Acceptance Criteria:
- [ ] Happy-path workflow tests no longer rely on auto-generated source stubs.
- [ ] Fresh workflow with only generated docs blocks with planning-validation
      failure and does not call the worker.
- [ ] The coverage index names the new planning validator evidence.

## Coverage Map

| PRD Promise | Covered By |
|---|---|
| P1 | ISS-2, ISS-4 |
| P2 | ISS-1, ISS-4 |
| P3 | ISS-1 |
| P4 | ISS-2, ISS-3 |
