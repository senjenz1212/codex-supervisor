# TDD Grill Findings: Reviewer Panel Adjudication

### Finding 1: First proof must hit workflow boundary

Status: resolved

Risk: Testing only an adjudication helper would miss whether Codex decision
composition, reviewer events, and replay exports actually use the packet.

Resolution: The first RED test is
`test_run_dual_agent_workflow_split_panel_triggers_adjudication`, which invokes
`run_dual_agent_workflow` with injected reviewers.

### Finding 2: Existing hard-block regression must be explicit

Status: resolved

Risk: The new adjudication decision could accidentally override the
conservative panel decision.

Resolution: The TDD plan includes
`test_real_reviewer_revise_still_hard_blocks_with_adjudication` and keeps real
important/critical revise/deny as hard-blocking.

### Finding 3: Replay/export is part of the public promise

Status: resolved

Risk: Adjudication could be present only on in-memory payloads.

Resolution: The TDD plan includes transcript/export assertions and requires an
`independent_reviewer_adjudication` event.

### Finding 4: Evidence inspection must be bounded

Status: resolved

Risk: "Tool-backed" could become unbounded filesystem access or external model
service calls in tests.

Resolution: The helper test requires bounded workspace-local ref checks and
status reporting for skipped or missing refs.
