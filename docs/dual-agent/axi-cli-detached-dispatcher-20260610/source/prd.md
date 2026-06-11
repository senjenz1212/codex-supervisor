# PRD: Detached Dispatcher And AXI CLI

## Problem Statement

Agent-facing supervisor calls currently have a transport-risk boundary: a poll or submit call can become the place where workflow execution work happens. That makes MCP stdio timeouts more likely, confuses operators about whether a dropped connection is a verdict, and lets status reads mutate workflow phase state. The supervisor needs one durable contract: submission reserves a job, the detached dispatcher executes phases, and every operator surface reads the ledger.

## Solution

Move workflow phase execution out of `poll_dual_agent_workflow_job` and keep request writing, worker spawning, heartbeat leases, stale-lease recovery, and terminal reconciliation inside the detached dispatcher process. Add `codex-supervisor-axi` as a short-lived CLI over the same submit, poll, and catch-up core used by MCP. The CLI prints compact TOON-style output by default, supports `--json`, gives next-step help, and preserves client token idempotency and receipt provenance sanitization.

## User Stories

- As an operator, I can submit a workflow through CLI or MCP and receive a durable `job_id` without waiting for model execution.
- As an operator, I can poll a running job and get status, lease, and recovery-point information in less than one second without triggering phase execution.
- As an operator, I can restart a dispatcher and see it resume from the recorded recovery point rather than relying on a live MCP session.
- As an automation, I can use `codex-supervisor-axi --json` for machine-readable status and `catch-up` for event-tail recovery after transport loss.

## PRD Promise Contracts

P1. Poll is read-only for phase execution. Representative action: call `poll_dual_agent_workflow_job(job_id)` while a job is `reserved` or `request_written`. Public boundary: MCP tool and AXI `poll/status`. Allowed outcome: durable row is reported with status, recovery point, pid, lease fields, and resume hint. Forbidden outcome: poll constructs a dispatcher, calls `run_once`, writes a request file, spawns a worker, or marks a missing worker terminal.

P2. Detached dispatcher is the only spawn owner. Representative action: run `codex-supervisor-workflow-dispatcher --once --job-id <job_id>`. Public boundary: dispatcher CLI and `WorkflowJobDispatcher`. Allowed outcome: dispatcher claims the row, writes the request, spawns the workflow worker, persists pid and recovery point, or parks/retries through existing lease policy. Forbidden outcome: MCP or AXI status paths perform those transitions.

P3. AXI CLI is a primary orchestration surface. Representative action: run `codex-supervisor-axi submit`, `poll`, `catch-up`, `gates`, `lessons`, or `trends`. Public boundary: console script and module main. Allowed outcome: one process invocation returns compact stdout with exit code 0 or structured error stdout with exit code 1. Forbidden outcome: interactive prompting, stderr-only errors, hidden state outside the ledger, or divergent idempotency from MCP.

P4. CLI output is operator-friendly and automatable. Representative action: run the CLI with no arguments, `--fields`, and `--json`. Public boundary: stdout. Allowed outcome: no-args view lists pending jobs and active gates with definitive empty states, default output is short, JSON contains equivalent data, and help lines provide next commands. Forbidden outcome: unbounded dumps, ambiguous empty output, or missing recovery command hints.

P5. Receipt provenance and client token semantics are unchanged. Representative action: submit via CLI with forged caller receipts and stable client token. Public boundary: stored request payload and workflow job row. Allowed outcome: caller-stamped `source=supervisor` or `evidence_grade=runtime_native` is downgraded, and duplicate token reattaches to the same job. Forbidden outcome: CLI accepts forged supervisor evidence or creates a second job for the same token.

P6. The launch model is documented. Representative action: an operator reads the dispatcher doc before installing launchd. Public boundary: docs. Allowed outcome: doc names the dispatcher command, plist path, and non-blocking responsibility split. Forbidden outcome: operators must infer whether MCP, AXI, or dispatcher owns phase execution.

## Implementation Decisions

Keep the existing durable job schema and dispatcher state machine. Remove MCP poll-side driving code rather than adding another flag. Implement AXI as an adapter around `CodexSupervisorMcpAPI` so submit, poll, and catch-up share the same idempotency, redaction, and receipt handling. Add read-only state listing helpers for CLI home, lessons, and trends. Add dispatcher `--job-id` only for targeted `--once` operation and test control; the long-lived dispatcher remains the normal production lane.

## Testing Decisions

Boundary tests must fail if poll invokes dispatcher code. CLI tests invoke the real `main(argv)` entrypoint, capture stdout and exit codes, and inspect the SQLite ledger for idempotency and provenance effects. Dispatcher tests keep fake `Popen` process fixtures so spawn behavior is deterministic. Full regression is the repository pytest suite plus `git diff --check`.

## Out Of Scope

This slice does not introduce Postgres, `SKIP LOCKED`, multi-dispatcher claiming, admission-control policy changes, automatic launchd installation, new gate semantics, fan-out default changes, or runtime adoption of Temporal/Restate/DBOS.
