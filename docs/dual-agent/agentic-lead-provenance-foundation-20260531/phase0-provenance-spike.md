# Phase 0 Provenance Spike: Agentic Lead Foundation

- task_id: `agentic-lead-provenance-foundation-20260531`
- date: `2026-05-31`
- runtime: `Claude Code 2.1.114`
- probe command output refs: raw Claude stream/debug captures were inspected
  locally, summarized below, and intentionally not committed because they include
  provider stream internals that are not needed for replay.

## Question

What durable, externally-readable evidence does Claude Code native agent fan-out expose to an external supervisor?

## Runtime Probe

I ran a tiny no-edit `claude -p` probe with `--output-format stream-json`, `--verbose`, `--no-session-persistence`, `--tools default`, and a low budget cap. The prompt asked Claude Code to use exactly one helper agent for a trivial read-only task, then report the externally-readable evidence available to a supervisor.

Observed stream events:

- `system.init` listed `Task` and `TaskOutput` tools, plus configured agents such as `Explore`.
- A native helper task completed with task id `a9cc333342755313d`.
- The stream emitted `task_updated` and `task_notification` events for that helper.
- The notification included an output file path under `/private/tmp/claude-501/.../tasks/a9cc333342755313d.output`, task status, summary, token usage, and duration.
- The final assistant result reported `PROBE_OK`.

## Finding

Native Claude Code fan-out is available locally, but the exposed helper evidence is not sufficient for high-stakes supervisor provenance:

- The external stream exposes a helper task id and completion notification.
- The helper output path is externally readable on the local machine but lives under ephemeral `/private/tmp`, not a supervisor-owned durable artifact path.
- The probe did not expose a stable supervisor-owned teammate transcript path.
- The evidence was emitted through the parent Claude process stream/debug files that Codex captured, not through a replay contract that P13 can independently trust by default.

## Decision

Use the hybrid Plan B:

- `agentic_lead_policy=allowed`: `/lead` may use native Claude fan-out for low/medium-stakes read-mostly work. Evidence grade is at most `lead_captured` unless Codex can independently verify a supervisor-owned capture path.
- `agentic_lead_policy=required`: Codex must require supervisor-owned worker evidence: per-worker transcript/output refs, hashes, timeout/budget caps, durable logs, runtime ids, and reviewer synthesis. `/lead` remains integrator, but Codex owns provenance.
- Do not make agentic mode default in this slice.

## Practical Provenance Bar

Evidence counts as high-grade only when the supervisor can derive the grade from where and how the artifact was captured. A grade declared in `/lead` prose or in the final outcome is ignored.
