# TDD Plan

## Public Boundary RED Tests

### test_agent_mailbox_message_carries_trace_fields

Maps to: P1
RED: Construct an `AgentMailboxMessage` with claims, objections, questions, tool receipts, evidence refs, raw transcript refs, persona id, addresses, and would-change-if. Assert `to_event_payload()` preserves them.
GREEN: Add typed fields with deterministic serialization.

### test_run_dual_agent_workflow_requires_test_and_diff_receipts_for_claims

Maps to: P2
RED: Fake Claude claims `tests passed` and `implemented` with `test_status=passed` and `changed_files`, but pass no receipts. Assert outcome review blocks.
GREEN: Require `test` and `git_diff` receipts with accepted status.

### test_run_dual_agent_workflow_verified_claims_string_does_not_satisfy_push

Maps to: P2
RED: Fake Claude claims `pushed` and caller supplies `verified_claims=["pushed"]` but no remote receipt. Assert outcome review blocks.
GREEN: Require a `git_remote` receipt for push claims.

### test_run_dual_agent_workflow_accepts_receipt_backed_claims

Maps to: P2
RED: Provide passed test, diff, and optional visual receipts. Assert workflow accepts and returns receipt details.
GREEN: Thread `tool_receipts` through MCP and workflow verification.

### test_workflow_resume_prompt_includes_trace_context

Maps to: P3
RED: Block a workflow and assert the resume prompt includes latest event id, steps, blocker, artifact output dir, and transcript command.
GREEN: Expand `workflow_resume_prompt`.

### test_export_dual_agent_run_artifacts_renders_interaction_receipts

Maps to: P4
RED: Insert a `dual_agent_interaction_message` with receipts and evidence refs, export artifacts, and assert Markdown includes each section.
GREEN: Render rich fields in `interactions.md` and `transcript.md`.

## TDD Grill

### Finding 1

status: resolved
question: Do tests hit public boundaries rather than helpers only?
resolution: Workflow tests go through `run_dual_agent_workflow`; export tests go through `export_gate_artifacts`; mailbox serialization has one direct unit test because schema preservation is the public contract for event payloads.

### Finding 2

status: resolved
question: Could screenshots still bypass visual verification?
resolution: This slice keeps current screenshot metadata checks and only adds receipt-backed claim verification. Cryptographic screenshot provenance remains out of scope.
