# Implementation Plan: Reviewer Panel Adjudication

## Files / Modules To Touch

- `supervisor/reviewer_registry.py` for deterministic adjudication trigger
  detection, strongest-objection selection, packet assembly, and bounded
  evidence-ref checks.
- `mcp_tools/codex_supervisor_stdio.py` for workflow decision wiring,
  adjudication event recording, transcript projection, and round objection
  wording.
- `supervisor/dual_agent_artifacts.py` for exported interactions/transcript
  rendering of adjudication packets.
- `supervisor/state.py` for the durable event allowlist used by catch-up and
  replay readers.
- `tests/test_dual_agent_workflow_driver.py` for public-boundary workflow tests
  covering split-panel adjudication, strong accept-shaped objection escalation,
  hard-block regression, bounded evidence checks, and export visibility.
- `docs/testing/public-boundaries.md` for the reviewer-panel adjudication public
  boundary.
- `docs/dual-agent/reviewer-panel-adjudication-20260601/` for PRD-to-TDD
  artifacts, receipts, workflow request/result exports, and replay artifacts.

## Steps

1. Add the workflow-boundary RED test
   `test_run_dual_agent_workflow_split_panel_triggers_adjudication` for P1,
   P3, and P4.
2. Add deterministic adjudication helpers in `supervisor/reviewer_registry.py`:
   trigger detection, strongest-objection ranking, packet construction, and
   bounded evidence checks.
3. Wire adjudication into `run_dual_agent_workflow` after
   `evaluate_reviewer_panel(...)` so real revise/deny hard blocks remain
   unchanged.
4. Add the workflow-boundary RED test
   `test_run_dual_agent_workflow_accept_with_strong_objection_escalates` for
   P2 and P4, then make accept-shaped important/critical objections escalate.
5. Record `independent_reviewer_adjudication` events and expose them through
   `read_gate_transcript`, `State.read_dual_agent_gate_events`, and exported
   markdown.
6. Add the regression/helper coverage
   `test_real_reviewer_revise_still_hard_blocks_with_adjudication` and
   `test_reviewer_panel_adjudication_checks_bounded_refs`.
7. Run focused reviewer-panel tests, legacy cursor-rejection regression,
   artifact/mailbox tests, compile checks, and the full suite.
8. Run the supervised dual-agent workflow to accepted outcome and export ledger
   replay artifacts.

## Risks

- A new adjudication decision could accidentally override the conservative
  hard-block rule. The implementation must only preserve a real block or add
  escalation for strong accept-shaped objections.
- Evidence inspection could become unbounded or nondeterministic. The helper
  must inspect only workspace-local refs under cwd, cap inspected refs, and
  mark external or out-of-root refs as skipped.
- Reviewer-unavailable recovery could be confused with disagreement
  adjudication. Missing/infrastructure verdict behavior must remain under the
  existing reviewer-unavailable policy and must never count missing verdicts as
  accept.
- Legacy replay readers still consume `tri_agent_cursor_review` and
  `cursor_review`. Adjudication metadata must be additive and must not remove
  existing payload fields.
- The TDD and outcome gates are sensitive to stale planning commands. Test names
  in the plan must match real tests or be explicitly removed before final
  review.

## Traceability

- P1 is covered by
  `test_run_dual_agent_workflow_split_panel_triggers_adjudication` and
  `test_independent_reviewer_adjudication_event_and_transcript_export`.
- P2 is covered by
  `test_run_dual_agent_workflow_accept_with_strong_objection_escalates`.
- P3 is covered by
  `test_real_reviewer_revise_still_hard_blocks_with_adjudication` plus the
  existing conservative panel revise regressions.
- P4 is covered by
  `test_run_dual_agent_workflow_split_panel_triggers_adjudication`,
  `test_independent_reviewer_adjudication_event_and_transcript_export`, and
  `test_reviewer_panel_adjudication_checks_bounded_refs`.
- Legacy no-weakening behavior is covered by
  `test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection`,
  `test_run_dual_agent_workflow_panel_missing_verdict_does_not_accept`,
  `test_second_reviewer_outage_proceeds_only_degraded`, and
  `test_panel_decision_is_exported_on_new_and_legacy_reviewer_events`.
