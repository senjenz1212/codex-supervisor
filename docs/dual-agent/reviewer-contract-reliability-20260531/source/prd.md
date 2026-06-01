# Reviewer Contract Reliability PRD

## Problem Statement

The reviewer-unavailable recovery slice made recoverable reviewer failures safe
to escalate or proceed degraded, but it did not make the independent reviewer
reliable. Live Cursor SDK runs with `composer-2.5` still return empty errored
transcripts and fail the typed `dual_agent_outcome` contract. That means the
workflow can be safe while still losing the independent cross-vendor verdict too
often.

## Solution

Make the independent reviewer model and output-enforcement path configurable,
defaulting to the Phase 0-proven structured LiteLLM reviewer route:
`gemini-3.1-pro-preview` with strict JSON-schema output. The reviewer must
still produce the same `Outcome` payload and pass the existing
`evaluate_outcome_fidelity` and critical-review completeness checks. The Cursor
SDK path remains available as a compatibility route, but genuine infrastructure
failures continue to flow through `reviewer_unavailable_policy`.

## User Stories

1. As an operator, I want the default independent reviewer to emit a parseable
   contract, so the gate normally gets a real third-party verdict.
2. As an auditor, I want the reviewer route, model, and output mode recorded, so
   I can replay why a verdict was considered valid.
3. As a gate owner, I want real reviewer `revise` or `deny` verdicts to keep
   blocking, so reliability work does not weaken review semantics.
4. As an operator, I want genuine SDK or gateway failures to remain recoverable,
   so transient infrastructure does not force fake verdicts.
5. As a supervisor maintainer, I want the model/output knobs to round-trip
   through config, MCP workflow calls, and the CLI fallback, so the route can be
   changed without code edits.

## PRD Promise Contracts

P1. Reviewer route is configurable
User-visible promise: The independent reviewer accepts configurable
`reviewer_model` and `reviewer_output_mode` values, with defaults chosen from
Phase 0 evidence.
Representative prompts or actions: Run `run_dual_agent_workflow` with Cursor
review enabled and no explicit reviewer model.
Public boundary: `run_dual_agent_workflow` and workflow CLI payload parsing.
Allowed outcomes: defaults are `gemini-3.1-pro-preview` and strict structured
output; explicit values are preserved; legacy `cursor_model` still works for
the Cursor SDK route.
Forbidden outcomes: hidden hard-coded reviewer model, dropped CLI fields, or
changing `lead_direct` defaults.
Related user stories: 1, 5

P2. Structured reviewer emits the same typed outcome contract
User-visible promise: The configured reviewer produces an `Outcome` that passes
the existing fidelity and critical-review completeness checks.
Representative prompts or actions: Invoke the reviewer on a representative
gate with Claude outcome JSON and evidence receipts.
Public boundary: `invoke_cursor_agent` / independent reviewer invocation.
Allowed outcomes: a valid `Outcome` with `Cursor Reviewer`, lowercase decision
tokens, required review fields, and no file edits.
Forbidden outcomes: accepting arbitrary prose, bypassing
`evaluate_outcome_fidelity`, or weakening required `critical_review` fields.
Related user stories: 1, 2

P3. Cross-lineage independence is preserved
User-visible promise: The default reviewer uses a non-Claude lineage while
Claude Code remains the `/lead` implementer.
Representative prompts or actions: Run a supervised workflow with
`execution_layer_mode=lead_direct` and Cursor review enabled.
Public boundary: workflow route event and reviewer tool-call metadata.
Allowed outcomes: reviewer metadata shows the Gemini LiteLLM route downstream
of the accepted Claude gate.
Forbidden outcomes: using Claude as both implementer and only independent
reviewer by default, or moving reviewer execution before Claude synthesis.
Related user stories: 1, 2

P4. Real reviewer rejection still blocks
User-visible promise: A valid reviewer `revise` or `deny` verdict blocks the
gate under the same AND algebra as before.
Representative prompts or actions: Use a fixture where the structured reviewer
returns `revise`.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: Codex records a blocking reviewer objection and reruns or
escalates through the normal gate path.
Forbidden outcomes: treating a valid rejection as unavailable infrastructure or
proceeding degraded.
Related user stories: 3

P5. Genuine infrastructure failure keeps recovery behavior
User-visible promise: SDK/gateway unreachable, model unavailable, or schema API
failure is classified as recoverable reviewer infrastructure and routed through
`reviewer_unavailable_policy`.
Representative prompts or actions: Configure an unreachable reviewer model and
run a workflow with reviewer review enabled.
Public boundary: independent reviewer invocation plus `run_dual_agent_workflow`
Allowed outcomes: `reviewer_infrastructure_unavailable` or
`reviewer_contract_unmet` is recorded and recovery policy applies.
Forbidden outcomes: fake accept verdicts, swallowed exceptions, or dead-end
blocks when default escalation should be resumable.
Related user stories: 4

P6. Replay and evidence remain deterministic
User-visible promise: Live reviewer evidence is captured as fixtures or
receipts, and deterministic tests do not require live model access.
Representative prompts or actions: Run the unit and workflow test suite without
gateway credentials.
Public boundary: test suite, replay artifacts, and exported workflow artifacts.
Allowed outcomes: deterministic fixtures cover success, rejection, and
infrastructure failure.
Forbidden outcomes: tests that require live network by default or transcripts
that omit reviewer route metadata.
Related user stories: 2, 5

## Implementation Decisions

- Add a reviewer configuration section to the supervisor config.
- Route independent review through either the existing Cursor SDK path or a
  new LiteLLM structured-output path based on `reviewer_output_mode`.
- Use strict JSON schema for the LiteLLM path, then validate by wrapping the
  JSON in the existing typed block and calling the same fidelity checks.
- Keep the read-only git-status guard around the whole reviewer invocation.
- Thread reviewer model/output fields through MCP workflow calls and the CLI
  fallback.
- Preserve `reviewer_unavailable_policy` for genuine reviewer infrastructure
  failures.

## Testing Decisions

The first RED tests hit public workflow and reviewer invocation boundaries.
External model calls are mocked below those boundaries, while Phase 0 live
evidence is kept as an artifact. Tests must prove happy-path contract success,
real rejection blocking, infrastructure recovery, config/CLI round-trip, and
metadata traceability.

## Out of Scope

This slice does not build a full reviewer registry, add multiple independent
reviewers, wire agentic fan-out, replace Claude Code `/lead`, remove
reviewer-unavailable recovery, or change P1/P2/P3/P13/P14 gate semantics.

## Further Notes

Phase 0 evidence is part of this PRD. The default decision is evidence-based:
Cursor SDK produced 0/8 typed outcomes across plan and agent modes, while the
LiteLLM Gemini structured route produced 3/3 fidelity-passing outcomes once the
schema constrained decision casing and the token budget was sufficient.
