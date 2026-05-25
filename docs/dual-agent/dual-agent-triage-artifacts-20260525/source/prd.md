# PRD: Dual-Agent Fast Triage Artifacts

## Problem

The dual-agent trace artifacts are honest and replay-oriented, but they still
require too much manual reconstruction during an incident. Operators can find
the failure code, agent messages, and tool timings, but they must jump across
`summary.json`, `interactions.md`, `transcript.jsonl`, and
`replay/manifest.json` to answer basic triage questions:

- what failed?
- which agent accepted or rejected?
- which tool call consumed time?
- what input/result shape did the tool see?
- what worktree state did the model inspect?
- what file should I open first?

## Goals

P1. Export a concise `triage.md` for every dual-agent artifact bundle.

P2. Make tool calls more forensic by recording safe `args`, `result_summary`,
`receipt_ids`, and `error` fields where the supervisor owns the invocation.

P3. Split Claude gate status from supervisor final status so accepted model
outcomes cannot be confused with accepted workflow completion.

P4. Add a minimal workspace snapshot to `replay/manifest.json`: git status,
diff hash, file-tree hash, source artifact hashes, and current HEAD.

P5. Make `index.md` point at source artifacts separately from gate documents,
so reviewers do not mistake "No PRD gate events" for "No PRD existed."

P6. Preserve the primary live-probe failure: if Claude times out or produces no
outcome, classify the run as the gate failure (`P2`/`P3`) instead of rewriting
it into a downstream receipt-verification failure.

## Non-Goals

- Do not store secrets, raw API keys, `.env` files, or full unbounded diffs.
- Do not replace `transcript.jsonl` as the machine replay source.
- Do not make Cursor the final acceptance boundary.
- Do not add cryptographic proof for screenshots or external tools.

## PRD Promise Contracts

### P1 - Triage Summary

- User-visible promise: every artifact export has a `triage.md` that names the
  verdict, failure code, MAST code, status split, top evidence, and next safe
  action.
- Public boundary: `supervisor.dual_agent_artifacts.export_dual_agent_run_artifacts`.
- Allowed outcomes: `triage.md` is compact and generated from ledger events.
- Forbidden outcomes: reviewer must scan 400+ lines before finding the blocker.

### P2 - Tool Call Forensics

- User-visible promise: trace-envelope tool calls include safe invocation
  context and result summaries where available.
- Public boundary: `supervisor.trace_envelope.stamp_trace_envelope` and
  `supervisor.dual_agent_runner.run_dual_agent_gate`.
- Allowed outcomes: args/results are summarized, redacted, and bounded.
- Forbidden outcomes: raw secrets or full prompts are copied into tool records.

### P3 - Status Split

- User-visible promise: artifacts distinguish `claude_gate_status` from
  `supervisor_final_status`.
- Public boundary: live failure-mode probe and artifact export.
- Allowed outcomes: Claude can be `accepted` while supervisor final is
  `blocked`.
- Forbidden outcomes: `start_dual_agent_gate: accepted` is mistaken for final
  workflow acceptance.

### P4 - Workspace Snapshot

- User-visible promise: replay manifest includes enough workspace state to
  reason about whether files changed without reconstructing the temp worktree.
- Public boundary: `replay/manifest.json`.
- Allowed outcomes: status, HEAD, diff hash, file-tree hash, and source hashes
  are captured.
- Forbidden outcomes: manifest stores raw secrets or relies only on deleted temp
  paths.

### P5 - Source Artifact Navigation

- User-visible promise: index separates gate documents from PRD/TDD source
  artifacts.
- Public boundary: `index.md`.
- Allowed outcomes: reviewers can open `source/prd.md` and `source/tdd.md`
  directly.
- Forbidden outcomes: top-level `prd.md` says no gate events and obscures the
  real source PRD.

### P6 - Primary Failure Classification

- User-visible promise: live probe artifacts name the first real blocking
  failure, even when the intended P11 receipt path is not reached.
- Public boundary: `scripts/probe_live_failure_mode.py`.
- Allowed outcomes: timeout/no-outcome runs classify as
  `P2/lead_invocation_timeout` or `P3/outcome_fidelity_*`.
- Forbidden outcomes: timeout/no-outcome runs are mislabeled as
  `P11/workflow_claim_verification_failed`.

## Acceptance Criteria

- Artifact export writes `triage.md` and lists it in `index.md`.
- `triage.md` contains failure code, MAST code, event id, status split, missing
  receipts, and next safe action for the live failure probe.
- `trace_envelope.tool_calls` retains timing and includes safe forensic fields.
- Live final payload contains `claude_gate_status` and
  `supervisor_final_status`.
- Replay manifest contains `workspace_snapshot`.
- Full test suite and live tri-agent probe pass.
- Timeout/no-outcome regression preserves `P2`/`P3` as the primary failure and
  marks P11 claim verification as not reached.
