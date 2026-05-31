# Agentic Lead Executor Wiring PRD

## Problem Statement

The agentic lead foundation can grade supervisor-owned worker receipts, but the workflow does not yet produce those receipts. Operators can request `agentic_lead_policy=required`, and the supervisor blocks before `/lead` when required evidence is absent, but a successful required path depends on caller-supplied receipts rather than supervisor-spawned workers. This keeps the quality boundary honest, yet it prevents dynamic execution from becoming a real acceleration mechanism.

## Solution

Wire supervisor-owned agentic worker execution into the workflow boundary. When an operator opts into `agentic_lead_policy=allowed` or `agentic_lead_policy=required`, Codex asks `/lead` for a bounded, machine-readable read-only worker roster, validates that roster against policy, runs `run_agentic_worker_fanout`, appends the resulting `dynamic_subagent_result` receipts, and reuses the existing P13 evidence-grade validation path. `/lead` remains the synthesis worker, while Codex owns worker spawning, artifact capture, hashes, cleanup, and replayable provenance.

## User Stories

1. As an operator, I want `agentic_lead_policy=required` to spawn supervisor-owned workers automatically, so that runtime-native evidence does not depend on manual receipt injection.
2. As a reviewer, I want `/lead` to plan the worker roster but not launch workers itself, so that provenance and permission boundaries stay controlled by Codex.
3. As a gate maintainer, I want P13 and P14 to keep using existing replay and synthesis logic, so that this slice connects the producer without duplicating the verifier.
4. As an operator, I want solo exceptions to apply only to artifact-only gates, so that a broad boolean cannot excuse solo execution in implementation or outcome review.
5. As a transport operator, I want submit/poll workflow payloads to carry agentic policy fields, so that detached workflow execution preserves the same policy contract as direct MCP execution.
6. As an operator, I want agentic worker fan-out to be bounded and cleaned up, so that acceleration does not leave orphaned worker processes or unbounded budget/runtime exposure.

## PRD Promise Contracts

P1. Supervisor produces runtime-native receipts
User-visible promise: A workflow with `agentic_lead_policy=required` can accept when Codex spawns read-only worker subprocesses and their output refs hash-replay under `.handoff/agentic-workers/`.
Representative prompts or actions: Run `run_dual_agent_workflow` with required agentic policy, at least one required role, and runtime-native evidence grade.
Public boundary: codex_supervisor_mcp
Allowed outcomes: supervisor calls `run_agentic_worker_fanout`, writes worker artifacts, appends receipts, P13 derives `runtime_native`, and the gate advances.
Forbidden outcomes: the workflow accepts required agentic policy with no supervisor-owned worker receipts, or requires the caller to hand-author runtime-native receipts.
Related user stories: 1, 3

P2. Lead plans, supervisor spawns
User-visible promise: `/lead` may produce a roster of worker specs, but only Codex validates and executes the roster.
Representative prompts or actions: Execution reaches the agentic producer path with a lead-planned roster containing role, persona, permission mode, tool pins, and prompt.
Public boundary: dual_agent_runner
Allowed outcomes: the roster is parsed and constrained before subprocess execution; workers run read-only with bounded timeout and budget.
Forbidden outcomes: `/lead` launches workers directly, worker specs bypass policy validation, or writable workers run in this slice.
Related user stories: 2

P3. Existing P13/P14 remain the verifier
User-visible promise: Producer-created receipts flow into the existing `verify_dynamic_workflow_receipts` and `_evaluate_agentic_lead_policy` path rather than a second evidence checker.
Representative prompts or actions: A required agentic workflow runs after workers finish.
Public boundary: dynamic_workflow_receipts
Allowed outcomes: receipt refs and hashes are replay-verified by the existing functions; declared evidence grades remain ignored.
Forbidden outcomes: a new verifier duplicates or weakens P13/P14, or `/lead` self-stamps `runtime_native`.
Related user stories: 3

P4. Solo exception is gate-type aware
User-visible promise: `solo_exception_for_artifact_only_gates` only excuses solo execution on gates classified as artifact-only.
Representative prompts or actions: Verify required policy with a solo lead receipt on `prd_review` and on `execution`.
Public boundary: dynamic_workflow_receipts
Allowed outcomes: artifact-only gates may use the exception; implementation and outcome gates still block solo execution.
Forbidden outcomes: setting the boolean excuses solo execution for every gate.
Related user stories: 4

P5. Detached workflow payload preserves policy
User-visible promise: The workflow CLI payload used by submit/poll carries all agentic policy fields through the same boundary as direct workflow execution.
Representative prompts or actions: Submit a durable workflow job with required agentic policy fields.
Public boundary: codex_supervisor_workflow_cli
Allowed outcomes: payload-to-kwargs retains policy, subagent counts, roles, solo exception, and evidence grade.
Forbidden outcomes: detached execution silently drops or resets policy fields.
Related user stories: 5

P6. Worker fan-out is bounded and cleaned up
User-visible promise: Supervisor-owned worker fan-out enforces per-agent timeout and budget caps and invokes orphan cleanup when worker processes time out or die.
Representative prompts or actions: Run an agentic workflow whose roster asks for excessive timeout/budget or whose worker subprocess times out.
Public boundary: dual_agent_runner
Allowed outcomes: roster validation rejects over-budget or over-timeout specs before launch; timed-out workers produce failed receipts, durable logs, and cleanup attempts.
Forbidden outcomes: a worker runs with unbounded budget or timeout, a timed-out worker is treated as successful evidence, or orphan cleanup is skipped after timeout/dead-worker detection.
Related user stories: 6

## Implementation Decisions

- Keep `agentic_lead_policy` defaulting to `off`; `lead_direct` without agentic policy is unchanged.
- Add a supervisor-owned producer path that runs before the synthesis `/lead` call when the operator opts into agentic policy.
- Make the producer read-only for this slice by rejecting writable worker permission modes before subprocess launch.
- Append executor receipts to the existing `tool_receipts` list and call the existing P13/P14 validation functions.
- Pass the current gate into agentic policy evaluation so artifact-only solo exceptions can be scoped precisely.
- Keep the fourth-reviewer registry, native Claude team fan-out, and eval runner outside this slice.

## Testing Decisions

Tests start at `codex_supervisor_mcp`, `dual_agent_runner`, `dynamic_workflow_receipts`, `agentic_workers`, and `codex_supervisor_workflow_cli`. The first RED proof must show a required agentic workflow invoking the supervisor-owned fan-out producer and accepting only after P13 derives runtime-native evidence from replayable refs. Regression tests must keep the missing-receipt required path blocked before synthesis.

## Out of Scope

This slice does not make agentic mode default, does not build the eval harness, does not add Gemini or another fourth reviewer, does not wire Claude native team fan-out, and does not implement raw MCP transport auto-reconnect.
