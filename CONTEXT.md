# Context

This project is a local supervisor for coding-agent sessions. The repository is
named `codex-supervisor`, but the product direction is target-agnostic: Codex
and Claude Code are both supervised through the same adapter boundary, with
Codex as the shipped default target (`target.kind: codex` in
`config.example.yaml`).

## Glossary

### Agent Supervisor

An always-on local control plane that observes coding-agent sessions, evaluates
their behavior, and optionally recommends or performs safe corrective actions.

### Target Agent

The coding agent being supervised. Codex and Claude Code are both implemented
target agents; the example configuration selects Codex by default.

### Target Agent Adapter

The boundary that hides target-specific session files, hook payloads, resume
commands, and settings formats from the supervisor core.

### Decision Runtime

The model-backed decision engine used for bounded expensive decisions such as
drift adjudication, post-run evaluation, and recovery planning. Claude Agent SDK
is the default implementation. Local Hermes is a future implementation.

### Normalized Event

A target-independent event consumed by supervisor logic. Raw Claude Code and
Codex payloads are normalized by adapters before entering drift detection,
replay, evaluation, or action policy.

### Scope Contract

The immutable run-start contract describing allowed paths, related paths,
protected paths, and never-touch patterns.

### Operating Mode

The per-capability safety mode: `off`, `shadow`, `advise`, or `enforce`.

### Replay

Re-running supervisor decisions from stored events, a run snapshot, model
outputs or fixtures, and the frozen scope contract.
