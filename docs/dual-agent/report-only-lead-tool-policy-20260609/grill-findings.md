# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 593396 `prd_review`: dontA[REDACTED_API_KEY] of --allowedTools is asserted only as a string, not behaviorally verified (downstream verify at execution/outcome)
- event_id 593396 `prd_review`: Detection is AND of report+deliverable markers, so report-only requests without a deliverable-marker path are conservative false-negatives
- event_id 593396 `prd_review`: shasum and pytest re-run were approval-denied this session; receipts are self_reported
- event_id 593397 `prd_review`: both agents accepted
- event_id 593484 `issues_review`: both agents accepted
- event_id 593647 `tdd_review`: cursor_review_failed: cursor_modified_worktree
- event_id 593717 `tdd_review`: git_diff and pytest receipts claim supervisor/dual_agent_lead.py is a changed file with passing tests, but current worktree has it clean at parent ce63a00 with no report-only branch in build_claude_lead_command, making test 1 RED
- event_id 593717 `tdd_review`: pytest receipts (23 passed / 4 passed) are contradicted by current source and must be re-run after the implementation is restored
- event_id 593718 `tdd_review`: agents have not both accepted yet; revise and continue
- event_id 593720 `tdd_review`: git_diff and pytest receipts claim supervisor/dual_agent_lead.py is a changed file with passing tests, but current worktree has it clean at parent ce63a00 with no report-only branch in build_claude_lead_command, making test 1 RED
- event_id 593720 `tdd_review`: pytest receipts (23 passed / 4 passed) are contradicted by current source and must be re-run after the implementation is restored
- event_id 593758 `tdd_review`: supervisor/dual_agent_lead.py is unmodified (diffstat = test files only, +117/-0); build_claude_lead_command:409-432 has no report-only branch and never emits --allowedTools/dontAsk.
- event_id 593758 `tdd_review`: test_report_only_execution_gate_command_includes_narrow_allowed_tools (invoker:506) is RED: asserts permission-mode==dontAsk and --allowedTools present, both false against current source.
- event_id 593758 `tdd_review`: Handoff receipt git-diff-report-only-lead-tool-policy-deliverables falsely lists supervisor/dual_agent_lead.py as a changed file.
- event_id 593758 `tdd_review`: pytest receipts ('4 passed','23 passed') are stale/false while test 1 is RED.
- event_id 593758 `tdd_review`: Blocker is unchanged from the prior REVISE round.
- event_id 593759 `tdd_review`: agents have not both accepted yet; revise and continue
- event_id 593761 `tdd_review`: supervisor/dual_agent_lead.py is unmodified (diffstat = test files only, +117/-0); build_claude_lead_command:409-432 has no report-only branch and never emits --allowedTools/dontAsk.
- event_id 593761 `tdd_review`: test_report_only_execution_gate_command_includes_narrow_allowed_tools (invoker:506) is RED: asserts permission-mode==dontAsk and --allowedTools present, both false against current source.
- event_id 593761 `tdd_review`: Handoff receipt git-diff-report-only-lead-tool-policy-deliverables falsely lists supervisor/dual_agent_lead.py as a changed file.
- event_id 593761 `tdd_review`: pytest receipts ('4 passed','23 passed') are stale/false while test 1 is RED.
- event_id 593761 `tdd_review`: Blocker is unchanged from the prior REVISE round.
- event_id 593799 `tdd_review`: git_diff receipt falsely claims supervisor/dual_agent_lead.py changed; git status shows it unmodified (only 2 test files, +117/-0)
- event_id 593799 `tdd_review`: pytest receipts '4 passed' and '23 passed' are stale: build_claude_lead_command uses request.permission_mode (:427) and request.tools (:431) with no dontAsk/--allowedTools branch, so test_report_only_execution_gate_command_includes_narrow_allowed_tools is RED
- event_id 593799 `tdd_review`: grep for REPORT_ONLY_EXECUTION_ALLOWED_TOOLS/_is_report_only/dontAsk in dual_agent_lead.py returns zero matches - report-only impl absent (reverted by cursor)
- event_id 593800 `tdd_review`: agents have not both accepted yet; revise and continue
- event_id 593802 `tdd_review`: git_diff receipt falsely claims supervisor/dual_agent_lead.py changed; git status shows it unmodified (only 2 test files, +117/-0)
- event_id 593802 `tdd_review`: pytest receipts '4 passed' and '23 passed' are stale: build_claude_lead_command uses request.permission_mode (:427) and request.tools (:431) with no dontAsk/--allowedTools branch, so test_report_only_execution_gate_command_includes_narrow_allowed_tools is RED
- event_id 593802 `tdd_review`: grep for REPORT_ONLY_EXECUTION_ALLOWED_TOOLS/_is_report_only/dontAsk in dual_agent_lead.py returns zero matches - report-only impl absent (reverted by cursor)
- event_id 594023 `tdd_review`: both agents accepted
- event_id 594286 `implementation_plan`: both agents accepted
- event_id 594352 `execution`: both agents accepted
- event_id 594786 `outcome_review`: both agents accepted
