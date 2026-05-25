# Issues: Dual-Agent Fast Triage Artifacts

## Issue 1 - Generate Triage Summary

PRD promise: P1
Public boundary: `export_dual_agent_run_artifacts`
Priority: P0
Estimate: S

Acceptance Criteria:

- [ ] `triage.md` is exported for any run with events.
- [ ] It names the failure code, MAST code, event id, and next safe action.
- [ ] It lists the slowest tool calls and evidence pointers.

## Issue 2 - Add Safe Tool Call Forensics

PRD promise: P2
Public boundary: `run_dual_agent_gate` and `stamp_trace_envelope`
Priority: P0
Estimate: M

Acceptance Criteria:

- [ ] Owned supervisor tool calls include bounded `args`.
- [ ] Owned supervisor tool calls include `result_summary`.
- [ ] Receipt-aware tool calls include `receipt_ids`.
- [ ] Exceptions captured by timed tool calls include `error`.

## Issue 3 - Split Status Semantics

PRD promise: P3
Public boundary: live failure-mode probe
Priority: P0
Estimate: S

Acceptance Criteria:

- [ ] Final payload has `claude_gate_status`.
- [ ] Final payload has `supervisor_final_status`.
- [ ] `triage.md` renders both.

## Issue 4 - Add Workspace Snapshot Manifest

PRD promise: P4
Public boundary: `replay/manifest.json`
Priority: P1
Estimate: M

Acceptance Criteria:

- [ ] Manifest records workspace root status and current HEAD when available.
- [ ] Manifest records git status and diff hash.
- [ ] Manifest records file-tree hash excluding secret/env/cache paths.
- [ ] Manifest records source artifact hashes.

## Issue 5 - Improve Artifact Navigation

PRD promise: P5
Public boundary: `index.md`
Priority: P1
Estimate: S

Acceptance Criteria:

- [ ] `index.md` lists `triage.md`.
- [ ] `index.md` has a separate source artifacts section.
- [ ] Top-level gate docs and source docs are not conflated.

## Issue 6 - Preserve Primary Live-Probe Failure

PRD promise: P6
Public boundary: `scripts/probe_live_failure_mode.py`
Priority: P0
Estimate: S

Acceptance Criteria:

- [ ] Timeout/no-outcome runs classify as `P2` or `P3`, not `P11`.
- [ ] P11 receipt-block remains the expected success path only after Claude
  accepts and produces an outcome.
- [ ] Live probe artifacts include `final_failure` and status split fields.
