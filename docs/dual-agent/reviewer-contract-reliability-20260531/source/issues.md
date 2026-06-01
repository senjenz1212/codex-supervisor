# Reviewer Contract Reliability Issues

## Slice ISS-1: Reviewer Config And Payload Surface

Type: AFK
Priority: P0
Estimate: S
Scope: Add reviewer model/output-mode config defaults and thread them through
MCP workflow parameters, job payloads, and the CLI fallback.
PRD promise: P1, P3
First public-boundary RED test: workflow CLI payload preserves
`reviewer_model` and `reviewer_output_mode`, and workflow invocation passes
them into the reviewer request.

Acceptance Criteria:

- [ ] Config default model is the Phase 0 winner.
- [ ] Config default output mode selects structured LiteLLM review.
- [ ] MCP and CLI workflow payloads preserve explicit reviewer fields.
- [ ] `lead_direct` and `reviewer_unavailable_policy` defaults are unchanged.

## Slice ISS-2: Structured LiteLLM Reviewer Invocation

Type: AFK
Priority: P0
Estimate: M
Scope: Add a structured LiteLLM reviewer execution path that emits schema JSON,
wraps it only for validation, and returns the existing `CursorInvocationResult`
shape.
PRD promise: P2, P3, P6
First public-boundary RED test: `invoke_cursor_agent` with structured output
mode and a fake OpenAI-compatible client returns a fidelity-passing outcome.

Acceptance Criteria:

- [ ] The same `Outcome` schema fields are required.
- [ ] Lowercase decision enums are enforced.
- [ ] `critical_review` completeness is still required.
- [ ] Reviewer route/model/output mode are present in result metadata.
- [ ] The reviewer remains read-only and downstream of Claude outcome input.

## Slice ISS-3: Reviewer Failure And Rejection Semantics

Type: AFK
Priority: P0
Estimate: M
Scope: Preserve real rejection blocking and classify structured route failures
through existing reviewer-unavailable recovery.
PRD promise: P4, P5
First public-boundary RED test: workflow with a valid structured `revise`
review blocks, while gateway failure is classified as reviewer infrastructure
unavailable.

Acceptance Criteria:

- [ ] Valid `revise` or `deny` outcomes are not recoverable infrastructure.
- [ ] Gateway/model failures are recoverable infrastructure.
- [ ] Missing/invalid structured output becomes `reviewer_contract_unmet`.
- [ ] Existing `proceed_degraded` and `escalate` recovery tests still pass.

## Slice ISS-4: Deterministic Evidence And Replay

Type: AFK
Priority: P0
Estimate: M
Scope: Add deterministic fixtures/tests for structured success, rejection, and
failure, plus Phase 0 artifacts as planning evidence.
PRD promise: P6
First public-boundary RED test: test suite verifies the structured reviewer
without live gateway credentials.

Acceptance Criteria:

- [ ] Live Phase 0 probe artifacts are committed.
- [ ] Unit tests mock model I/O below the reviewer boundary.
- [ ] Workflow tests assert route metadata and degraded recovery behavior.
- [ ] Full suite can run without live Cursor or LiteLLM credentials.

## Coverage Map

| Promise | Covering slices |
|---|---|
| P1 | ISS-1 |
| P2 | ISS-2 |
| P3 | ISS-1, ISS-2 |
| P4 | ISS-3 |
| P5 | ISS-3 |
| P6 | ISS-2, ISS-4 |
