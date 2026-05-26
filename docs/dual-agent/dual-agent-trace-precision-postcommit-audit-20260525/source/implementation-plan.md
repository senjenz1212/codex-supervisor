# Implementation Plan: Trace Precision Post-Commit Tri-Agent Audit

## Files / Modules To Touch

- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/source/prd.md`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/source/issues.md`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/source/tdd.md`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/source/grill-findings.md`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/source/implementation-plan.md`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/summary.json`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/transcript.jsonl`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/interactions.md`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/triage.md`
- `docs/dual-agent/dual-agent-trace-precision-postcommit-audit-20260525/replay/manifest.json`
- `tests/fixtures/dual_agent/dual_agent_trace_precision_postcommit_audit_20260525/summary.json`

## Steps

1. Repair the planning source packet so the deterministic validator accepts it
   for `gate="outcome_review"`.
2. Run a live Claude Code outcome-review gate against commit `f9a6f81` with the
   repaired planning artifacts.
3. If Claude returns a typed outcome, invoke Cursor as an independent read-only
   reviewer against the same commit target and Claude outcome.
4. Write Codex's final audit verdict, addressing the Claude and Cursor event
   ids and naming whether production-code changes are required.
5. Export the supervisor ledger into the audit artifact directory and fixture
   directory.
6. Inspect the generated JSONL, manifest, markdown projections, and summary for
   missing links or reviewer skips.
7. Run local validation, compile checks, diff hygiene, and secret scans before
   staging the intended audit artifacts.

## Risks

- Claude could deny the gate with a real issue in commit `f9a6f81`; if that
  happens, stop treating this as artifact-only work and patch the defect in a
  separate implementation slice.
- Cursor may be unavailable or reject the review due to SDK/runtime problems;
  the artifact must record that as a real skip or failure, not as tri-agent
  success.
- The generated transcripts may include machine-specific paths or secret-shaped
  text; run redaction and scan before committing.
- The existing blocked planning-validation attempt can confuse reviewers unless
  the superseding successful run clearly records the final status and event ids.

## Traceability

- P1 / Slice 1 -> `test_postcommit_audit_captures_claude_review_event`
  verifies Claude's response event is real and ledger-backed.
- P2 / Slice 2 -> `test_postcommit_audit_captures_cursor_review_event`
  verifies Cursor's independent review event is real or the skip is explicit.
- P3 / Slice 3 -> `test_postcommit_audit_links_codex_verdict_to_agent_events`
  verifies Codex's final verdict addresses the reviewer events and exports to
  replay artifacts.
- P4 / Slice 4 -> `test_postcommit_audit_rejects_secret_or_unrelated_artifacts`
  verifies hygiene checks prevent secrets and unrelated files from being staged.
