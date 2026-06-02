# Implementation Plan: Reviewer Panel Conservative Aggregator

## Files / Modules To Touch

- `supervisor/config.py` for the low-confidence threshold setting.
- `config.example.yaml` for operator-visible default configuration.
- `supervisor/reviewer_registry.py` or a small adjacent reviewer panel module
  for deterministic conservative panel evaluation.
- `mcp_tools/codex_supervisor_stdio.py` for workflow decision wiring, reviewer
  event payloads, and Codex review metadata.
- `supervisor/agent_mailbox.py` if the Codex review packet needs panel decision
  awareness beyond the legacy reviewer accepted flag.
- `tests/test_dual_agent_workflow_driver.py` for public-boundary RED/GREEN
  coverage of block, missing, accept, low-confidence, recovery, and event
  export semantics.
- `docs/dual-agent/reviewer-panel-conservative-aggregator-20260601/` for
  planning and evidence artifacts.

## Steps

1. Add RED workflow tests for serious revise/deny blocking and high-confidence
   accept throughput.
2. Add the conservative panel evaluator and use it in the workflow decision
   path while preserving compatibility payloads.
3. Add missing-verdict and low-confidence tests, then introduce the tunable
   threshold.
4. Extend reviewer event payloads and transcript-facing data with
   `independent_reviewer_panel_decision`.
5. Re-run reviewer-unavailable recovery tests to prove classified
   infrastructure failures remain under the existing recovery policy.
6. Run focused and full validation, then execute the supervisor workflow gates.

## Risks

- The old `cursor_decision` compatibility field is widely referenced in
  transcripts and objections. The implementation must preserve the field while
  deriving it from the panel decision.
- Low-confidence escalation can create false blocks if the default is too
  aggressive. The default must be permissive, and tests must show ordinary
  accepts still advance.
- Reviewer-unavailable recovery already has subtle high-stakes rails. The panel
  evaluator must not turn a classified infrastructure failure into a generic
  quality objection or bypass existing recovery receipts.
- Event payload changes can break replay/export consumers if the new decision is
  only written on one event kind. The new and legacy reviewer events both need
  the same aggregate decision metadata.

## Traceability

- P1 is covered by
  `test_run_dual_agent_workflow_panel_blocks_important_reviewer_revise`.
- P2 is covered by
  `test_run_dual_agent_workflow_panel_missing_verdict_does_not_accept`.
- P3 is covered by
  `test_run_dual_agent_workflow_panel_high_confidence_accept_advances_by_default`.
- P4 is covered by
  `test_run_dual_agent_workflow_panel_low_confidence_accept_escalates_when_threshold_configured`.
- P5 is covered by
  `test_reviewer_unavailable_recovery_still_proceeds_degraded_with_panel_metadata`.
- Event/replay visibility is covered by
  `test_panel_decision_is_exported_on_new_and_legacy_reviewer_events`.
