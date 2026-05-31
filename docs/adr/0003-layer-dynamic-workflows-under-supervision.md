# ADR 0003: Layer Dynamic Workflows Under The Supervisor

## Status

Accepted

## Context

Native dynamic workflows can improve fan-out execution tasks such as codebase
audits, large migrations, Cortex four-reviewer fan-out, and eval-fixture
population. They do not replace the dual-agent supervision contract. The
supervisor ledger, Codex gate runner, lead worker, handoff packet, receipt
checks, and outcome validation remain the acceptance surface.

The preview surface is young enough that trusting it inside the gated loop
without extra checks would make the workflow less auditable, especially at high
subagent counts.

## Decision

Keep Codex plus lead as the supervision layer.

Allow dynamic workflows only as an execution-layer preview behind the lead
worker. The lead may use dynamic workflow fan-out for the allowed task classes,
but Codex still supervises the final artifact through the ordinary gate,
machine-readable outcome block, receipt verification, Cursor review when
enabled, and replayable ledger artifacts.

Before a dynamic workflow execution path is trusted in the gated loop, it must
produce evidence for all preview gates:

- per-subagent budget caps are verified;
- permission mode and tool pins are verified per subagent;
- output is machine-readable and parseable by Slice-0 probes;
- headless operation works with `--no-session-persistence`;
- replay or CI determinism is available;
- a throwaway-worktree comparison records cost, quality, and auditability
  against the single-lead path.

## Consequences

- The current dual-agent workflow remains the control and audit interface.
- Dynamic workflows can be tried cheaply and reversibly without becoming the
  truth surface.
- Fan-out work gets a real execution seam while routine gates stay on the
  simpler lead-direct path.
- The first production use of dynamic workflows must carry the preview-gate
  receipts in the handoff packet and final outcome claims.
