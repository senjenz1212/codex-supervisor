# PRD: RUNTIME-001 Provider-Neutral Runtime Seams

## Problem

Agent lifecycle and model calls are coupled to provider-specific SDKs and CLI
shapes. That prevents controlled A/B/C execution, makes cancellation and
result schemas provider-dependent, and risks mixing agent execution with the
`TargetAgentAdapter` observation/steering boundary.

## Goal

Route agent lifecycle through a separate `AgentRuntime` and non-agent model
calls through `ModelClient`, while preserving provider-specific code only in
adapters/transports.

## Promise Contracts

### P1: Lifecycle is separate from observation

- Public boundary: `AgentRuntime`.
- Operations: `start`, `resume`, `cancel`, `stream`, and `collect`.
- `TargetAgentAdapter` is not extended with invocation lifecycle.

### P2: Runtime results are provider-neutral

- Public boundary: `AgentRunResult`.
- Claude Code and Codex return the same schema, terminal taxonomy, hashes,
  timestamps, cost field, resolved model field, and metadata envelope.

### P3: Capabilities are explicit

- Public boundary: `AgentRunHandle.capabilities`.
- Resume, cancellation, streaming, cost reporting, subagents, and image support
  are declared per runtime rather than inferred by callers.

### P4: Model calls use one typed seam

- Public boundary: `ModelClient.complete` and `structured_complete`.
- Responses record provider and resolved model; invalid structured output fails
  closed.

### P5: Provider code is confined to adapters

- Public boundary: runtime/model construction at composition roots.
- Core experiment, drift, review, planning, and recovery modules do not import
  provider SDKs or construct provider clients directly.

### P6: Cancellation is lifecycle-safe

- Runtime cancellation reaches the underlying transport/process group and
  collection returns a deterministic cancelled/failed terminal result.

## Non-goals

- Adding lifecycle methods to `TargetAgentAdapter`.
- Claiming live Claude Code or Codex execution from fake transports.
- Selecting a preferred provider or model.
