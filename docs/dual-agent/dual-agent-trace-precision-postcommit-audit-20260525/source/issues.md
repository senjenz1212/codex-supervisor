# Issues: Trace Precision Post-Commit Tri-Agent Audit

## Slice 1 - Capture Claude Post-Commit Review

PRD promise: P1
Public boundary: `supervisor.dual_agent_runner.run_dual_agent_gate`
Priority: P0
Estimate: S

Scope: Run the live Claude Code `outcome_review` gate against commit `f9a6f81`
with all planning artifacts attached and with expectations for a structured
accept or deny decision.

Acceptance Criteria:

- [ ] Claude receives instructions to inspect commit `f9a6f81` and the
  trace-precision files touched by that commit.
- [ ] The supervisor writes a `dual_agent_interaction_message` from Codex to
  Claude and a response event from Claude to Codex.
- [ ] Claude's outcome includes decision, confidence, confidence rationale,
  claims, evidence references, and any objections.

## Slice 2 - Capture Cursor Independent Review

PRD promise: P2
Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`
Priority: P0
Estimate: S

Scope: Invoke Cursor after Claude returns an outcome, passing the same audit
target and Claude outcome JSON while requiring Cursor to stay read-only.

Acceptance Criteria:

- [ ] Cursor receives Claude's outcome and the same commit-level review target.
- [ ] The supervisor writes a `tri_agent_cursor_review` event with accepted or
  rejected status.
- [ ] Cursor's model, run id, agent id, duration, transcript tail, and typed
  outcome are exported or the skip reason is explicit.

## Slice 3 - Preserve Codex Final Verdict And Replay Artifacts

PRD promise: P3
Public boundary: `supervisor.dual_agent_artifacts.export_dual_agent_run_artifacts`
Priority: P0
Estimate: S

Scope: Record Codex's final acceptance or blockage after reading Claude,
Cursor, and local verification, then export the ledger into human-readable and
machine-readable artifacts.

Acceptance Criteria:

- [ ] Codex final event states whether any production-code patch was required.
- [ ] `summary.json`, `transcript.jsonl`, `interactions.md`, `triage.md`, and
  `replay/manifest.json` exist in the audit artifact directory.
- [ ] Event addresses link Codex's final decision back to Claude and Cursor
  review events.

## Slice 4 - Validate Audit Hygiene

PRD promise: P4
Public boundary: repository command line and git diff
Priority: P1
Estimate: S

Scope: Run local validation after artifact generation, verify no secret-shaped
tokens are present, and stage only the intended audit files.

Acceptance Criteria:

- [ ] The source packet passes `validate_planning_artifacts` for
  `gate="outcome_review"`.
- [ ] Focused tests, compile checks, and `git diff --check` pass or any failure
  is documented in the final report.
- [ ] Generated audit artifacts contain no raw Cursor, OpenAI, GitHub, or
  bearer-token-shaped secrets.
- [ ] The unrelated Vela planning directory remains unstaged and untouched.
