# Implementation Plan: Report-Only Lead Tool Policy

## Files / Modules To Touch

- `supervisor/dual_agent_lead.py`
- `tests/test_dual_agent_lead_invoker.py`
- `tests/test_dual_agent_workflow_driver.py`
- `docs/dual-agent/report-only-lead-tool-policy-20260609/source/prd.md`
- `docs/dual-agent/report-only-lead-tool-policy-20260609/source/issues.md`
- `docs/dual-agent/report-only-lead-tool-policy-20260609/source/tdd.md`
- `docs/dual-agent/report-only-lead-tool-policy-20260609/source/grill-findings.md`
- `docs/dual-agent/report-only-lead-tool-policy-20260609/source/grill-findings-tdd.md`

## Risks

- A broad permission fix could grant normal code tasks extra write or shell
  access. The negative command test keeps the policy attached only to explicit
  report-only execution requests.
- A report receipt could be mistaken for a deliverable change. The negative P11
  workflow-driver test keeps changed-files evidence mandatory.
- Marker-based detection could miss some future report-only phrasing. This slice
  covers the Vela AutoResearch shape that triggered the issue and leaves broader
  routing for a later product decision.

## Traceability

- P1 -> test_report_only_execution_gate_command_includes_narrow_allowed_tools
- P2 -> test_normal_execution_gate_command_does_not_get_report_only_allowed_tools
- P3 -> test_execution_gate_allows_vela_style_report_only_artifact_with_receipt
- P3 -> test_execution_gate_blocks_vela_style_report_receipt_without_changed_file

## Steps

1. Add `REPORT_ONLY_EXECUTION_ALLOWED_TOOLS`,
   `REPORT_ONLY_EXECUTION_PERMISSION_MODE`, and route only explicit report-only
   execution requests to that command policy.
2. Keep normal execution command construction unchanged by default.
3. Add command-boundary tests for report-only and normal execution.
4. Add workflow-driver tests proving P11 accepts real report deliverables and
   blocks receipt-only report claims.
5. Run focused tests, targeted files, and `git diff --check`.
