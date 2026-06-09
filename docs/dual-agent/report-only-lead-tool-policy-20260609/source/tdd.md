# TDD Plan: Report-Only Lead Tool Policy

## test_report_only_execution_gate_command_includes_narrow_allowed_tools

Maps to: P1, Slice 1

RED: Build a `LeadInvocationRequest` for the execution gate with a report-only
Vela AutoResearch instruction that names a `docs/dual-agent/...` report
deliverable. Assert that `build_claude_lead_command` includes `--allowedTools`
with edit/write, git status/diff, and pytest command patterns. The test fails
before the command builder adds a report-only allowed-tools branch and before it
switches that branch away from global bypass.

GREEN: Add the report-only detection and narrow allowed-tools tuple in
`supervisor/dual_agent_lead.py`, then confirm the command uses `dontAsk` and
includes only the expected report-authoring and receipt-capture tools.

## test_normal_execution_gate_command_does_not_get_report_only_allowed_tools

Maps to: P2, Slice 1

RED: Build a normal execution-gate request for a code change and assert
`--allowedTools` is absent. This protects the default code-task invocation from
receiving the report-only write policy.

GREEN: Scope the allowed-tools branch to execution requests with both
report-only and docs/report deliverable markers.

## test_execution_gate_allows_vela_style_report_only_artifact_with_receipt

Maps to: P3, Slice 2

RED: Run the workflow-driver harness with a Vela-style report path listed in
`changed_files` and in a report receipt. Before the existing P11 report artifact
path is exercised by this regression, the slice lacks proof that the intended
report shape can pass.

GREEN: Confirm P11 returns green, reports the deliverable file, and records the
docs/report file as the covered report artifact.

## test_execution_gate_blocks_vela_style_report_receipt_without_changed_file

Maps to: P3, Slice 2

RED: Run the same workflow-driver harness with the report receipt but an empty
`changed_files` list. The expected outcome is blocked with
`accepted_gate_without_changed_files`, proving a receipt alone cannot satisfy
P11.

GREEN: Keep `verify_gate_deliverable_evidence` strict and verify the regression
continues to block.
