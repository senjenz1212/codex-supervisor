# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 764629 `prd_review`: gate blocked
- event_id 764757 `prd_review`: both agents accepted
- event_id 764793 `issues_review`: both agents accepted
- event_id 764872 `tdd_review`: LOW: P3 tests t1-t3 (cross-surface reattach) likely pass at authoring since shared-core CodexSupervisorMcpAPI client_token idempotency already exists at axi:209; they function as preservation guards, but cross-surface ordering and dual-surface catch-up equivalence are net-new coverage - not blocking
- event_id 764872 `tdd_review`: LOW: t4 partially GREEN (detached_dispatcher_only already set stdio:5050) but its --json help and forbidden-runner/no-request-file assertions are net-new RED
- event_id 765051 `tdd_review`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 765057 `tdd_review`: LOW: P3 tests t1-t3 (cross-surface reattach) likely pass at authoring since shared-core CodexSupervisorMcpAPI client_token idempotency already exists at axi:209; they function as preservation guards, but cross-surface ordering and dual-surface catch-up equivalence are net-new coverage - not blocking
- event_id 765057 `tdd_review`: LOW: t4 partially GREEN (detached_dispatcher_only already set stdio:5050) but its --json help and forbidden-runner/no-request-file assertions are net-new RED
- event_id 765116 `tdd_review`: Low-severity: 3 of 6 tests (t1-t3, P3) are GREEN at authoring time because shared-core client_token idempotency and read-only catch-up already exist; they function as regression/preservation guards, not RED-first drivers. Does not block.
- event_id 765298 `tdd_review`: cursor_review_failed: Material: t3 catch-up equivalence requires no writes but AXI public catch-up writes transport_incident_observed (axi.py:244-254); MCP core read is read-only; Process: tdd.md unchanged after reviewer-1 REVISE; Claude re-review did not address the specific catch-up contradiction (FM-2.4); Low: t1-t3 partly GREEN-at-authoring as preservation guards; acceptable if t3 is fixed
- event_id 765304 `tdd_review`: Low-severity: 3 of 6 tests (t1-t3, P3) are GREEN at authoring time because shared-core client_token idempotency and read-only catch-up already exist; they function as regression/preservation guards, not RED-first drivers. Does not block.
- event_id 765399 `tdd_review`: t3 (test_axi_and_mcp_catch_up_return_equivalent_event_tail, tdd.md:30-34) asserts no event is written by either read and GREEN 'keep catch-up as read-only event-tail query', but public AXI catch-up _catch_up (axi.py:238-258, dispatched axi:577, subparser axi:511) calls record_transport_incident(incident_type=catch_up_invoked, interface=axi) at axi.py:244-254, writing transport_incident_observed every invocation. The shared core catch_up_dual_agent_workflow (axi:239-243) is read-only, but the test's declared public boundary is codex_supervisor_axi (tdd.md:28), which is not read-only. Test cannot reach GREEN as written.
- event_id 765399 `tdd_review`: tdd.md sha 868bcee2 is byte-identical to the round where independent reviewer-1 issued REVISE; resubmitting unchanged over a verified material objection is FM-1.3 step-repetition / FM-2.4 information-withholding.
- event_id 765399 `tdd_review`: Required fix (scoped to t3, tdd.md must change off 868bcee2): either (a) re-scope t3 to assert AXI public catch-up writes exactly one catch_up_invoked event while the core/MCP read path stays read-only and the returned event tails are equivalent; or (b) if PRD requires catch-up read-only, change t3 GREEN to remove the AXI catch_up_invoked write and propagate to issues.md/implementation-plan.md.
- event_id 765400 `tdd_review`: agents have not both accepted yet; revise and continue
- event_id 765402 `tdd_review`: t3 (test_axi_and_mcp_catch_up_return_equivalent_event_tail, tdd.md:30-34) asserts no event is written by either read and GREEN 'keep catch-up as read-only event-tail query', but public AXI catch-up _catch_up (axi.py:238-258, dispatched axi:577, subparser axi:511) calls record_transport_incident(incident_type=catch_up_invoked, interface=axi) at axi.py:244-254, writing transport_incident_observed every invocation. The shared core catch_up_dual_agent_workflow (axi:239-243) is read-only, but the test's declared public boundary is codex_supervisor_axi (tdd.md:28), which is not read-only. Test cannot reach GREEN as written.
- event_id 765402 `tdd_review`: tdd.md sha 868bcee2 is byte-identical to the round where independent reviewer-1 issued REVISE; resubmitting unchanged over a verified material objection is FM-1.3 step-repetition / FM-2.4 information-withholding.
- event_id 765402 `tdd_review`: Required fix (scoped to t3, tdd.md must change off 868bcee2): either (a) re-scope t3 to assert AXI public catch-up writes exactly one catch_up_invoked event while the core/MCP read path stays read-only and the returned event tails are equivalent; or (b) if PRD requires catch-up read-only, change t3 GREEN to remove the AXI catch_up_invoked write and propagate to issues.md/implementation-plan.md.
- event_id 765588 `tdd_review`: both agents accepted
- event_id 765613 `implementation_plan`: Low-severity: per-file placement of the 6 tests is implicit; plan owns test_dual_agent_workflow_driver.py without naming which tests it hosts. Non-blocking - file set is a justified superset and TDD boundaries make placement derivable.
- event_id 765755 `implementation_plan`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 765761 `implementation_plan`: Low-severity: per-file placement of the 6 tests is implicit; plan owns test_dual_agent_workflow_driver.py without naming which tests it hosts. Non-blocking - file set is a justified superset and TDD boundaries make placement derivable.
- event_id 765813 `implementation_plan`: FM-1.3 step-repetition conditions are met (identical handoff+artifacts+source), disclosed not withheld; does not block because prior gate ACCEPTED and source has not regressed
- event_id 765981 `implementation_plan`: both agents accepted
- event_id 766190 `execution`: gate blocked
- event_id 766424 `execution`: both agents accepted
- event_id 766670 `outcome_review`: both agents accepted
