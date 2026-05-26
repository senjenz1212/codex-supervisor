# TDD Plan: Trace Precision Post-Commit Tri-Agent Audit

## test_postcommit_audit_captures_claude_review_event

Maps to: P1 / Slice 1
Boundary: `supervisor.dual_agent_runner.run_dual_agent_gate`

RED: Run the audit without a live Claude outcome and assert the exported
transcript has no `claude_code -> codex` response event, proving the audit
cannot claim Claude participation by prose alone.

GREEN: Run the live `outcome_review` gate with the source packet attached and
assert `transcript.jsonl` includes a `dual_agent_interaction_message` from
`claude_code` to `codex` with decision, confidence criteria, claims, and
evidence references.

## test_postcommit_audit_captures_cursor_review_event

Maps to: P2 / Slice 2
Boundary: `supervisor.cursor_agent.invoke_cursor_agent`

RED: Export artifacts after Claude only and assert no `tri_agent_cursor_review`
event exists, proving the audit cannot call itself tri-agent until Cursor is
persisted or explicitly skipped.

GREEN: Invoke Cursor with Claude's outcome and assert `transcript.jsonl`
contains `tri_agent_cursor_review` with accepted or rejected status, model,
duration, typed outcome, and transcript tail.

## test_postcommit_audit_links_codex_verdict_to_agent_events

Maps to: P3 / Slice 3
Boundary: `supervisor.dual_agent_artifacts.export_dual_agent_run_artifacts`

RED: Write a final Codex event without `addresses` and assert the replay trace
cannot link the verdict back to the reviewer events.

GREEN: Write a final `dual_agent_gate_result` and `dual_agent_interaction_message`
whose addresses reference Claude and Cursor event ids, then assert
`summary.json`, `triage.md`, `interactions.md`, and `replay/manifest.json`
render the final verdict and links.

## test_postcommit_audit_rejects_secret_or_unrelated_artifacts

Maps to: P4 / Slice 4
Boundary: repository command line and git diff

RED: Add a secret-shaped token or unrelated untracked path to the candidate
artifact list and assert the hygiene check fails before staging.

GREEN: Run the final scan over the generated audit directory and diff, assert
no secret-shaped strings are present, and assert only the intended
post-commit-audit artifact paths are candidates for staging.
