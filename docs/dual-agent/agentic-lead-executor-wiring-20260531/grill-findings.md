# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 309221 `prd_review`: P6/ISS-2/ISS-5 'budget caps' has no unit or default value, making over-budget rejection untestable as written
- event_id 309221 `prd_review`: allowed agentic_lead_policy path is in scope but every acceptance criterion is framed for required; allowed semantics underspecified
- event_id 309221 `prd_review`: anti-over-credit provenance guard appears only in ISS-3 acceptance criteria, not in PRD promise contracts despite being explicit in the intent
- event_id 309222 `prd_review`: both agents accepted
- event_id 309286 `issues_review`: Over-budget/over-timeout rejection-before-launch is an acceptance criterion shared by ISS-2 and ISS-5 but has no 1:1 named test in the TDD Test Cases section (only in the RED/GREEN narrative); ownership is ambiguous between the two slices.
- event_id 309286 `issues_review`: Handoff intent implies run_dual_agent_workflow is near dual_agent_runner.py; dual_agent_runner.py contains none of the named symbols and the function actually resides in mcp_tools/codex_supervisor_stdio.py.
- event_id 309287 `issues_review`: both agents accepted
- event_id 309449 `tdd_review`: Test #5 test_declared_runtime_native_from_non_supervisor_path_is_downgraded_and_blocked risks fake-RED or duplicating test #6: code already derives grade (declared_evidence_grade at line 310 is captured but unused for verdict) and already downgrades refs outside the 3 prefixes; a ref under docs/dual-agent/ is test #6, a ref outside all prefixes is already self_reported today
- event_id 309449 `tdd_review`: Test #5 RED language is hedged ('can be tricked OR lacks this explicit regression'), tacitly admitting it may be characterization rather than a failing RED - a tdd_review gate should require this be resolved
- event_id 309455 `tdd_review`: cursor_review_failed: missing dual_agent_outcome block
- event_id 309457 `tdd_review`: Test #5 test_declared_runtime_native_from_non_supervisor_path_is_downgraded_and_blocked risks fake-RED or duplicating test #6: code already derives grade (declared_evidence_grade at line 310 is captured but unused for verdict) and already downgrades refs outside the 3 prefixes; a ref under docs/dual-agent/ is test #6, a ref outside all prefixes is already self_reported today
- event_id 309457 `tdd_review`: Test #5 RED language is hedged ('can be tricked OR lacks this explicit regression'), tacitly admitting it may be characterization rather than a failing RED - a tdd_review gate should require this be resolved
- event_id 309519 `tdd_review`: Boundary/file naming mismatch: plan names dual_agent_runner; run_dual_agent_workflow is defined in mcp_tools/codex_supervisor_stdio.py:404,2526 with workflow-start verify at :549,:2118 - implementer could wire the producer in the wrong module.
- event_id 309519 `tdd_review`: test_declared_runtime_native_from_non_supervisor_path_is_downgraded_and_blocked overlaps test_docs_dual_agent_refs and risks green-on-arrival: _derive_evidence_grade already downgrades non-prefixed refs (dynamic_workflow_receipts.py:343-354), yet the RED/GREEN section lists it under RED.
- event_id 309519 `tdd_review`: Cleanup lifecycle gap: cleanup_orphaned_agentic_workers consumes pid/started_at_s (agentic_workers.py:216-241) but blocking run_agentic_worker_fanout captures TimeoutExpired inline (status='timeout', no pid emitted), so producer-wired cleanup may have nothing to clean after a fan-out timeout.
- event_id 309523 `tdd_review`: cursor_review_failed: missing dual_agent_outcome block
- event_id 309525 `tdd_review`: Boundary/file naming mismatch: plan names dual_agent_runner; run_dual_agent_workflow is defined in mcp_tools/codex_supervisor_stdio.py:404,2526 with workflow-start verify at :549,:2118 - implementer could wire the producer in the wrong module.
- event_id 309525 `tdd_review`: test_declared_runtime_native_from_non_supervisor_path_is_downgraded_and_blocked overlaps test_docs_dual_agent_refs and risks green-on-arrival: _derive_evidence_grade already downgrades non-prefixed refs (dynamic_workflow_receipts.py:343-354), yet the RED/GREEN section lists it under RED.
- event_id 309525 `tdd_review`: Cleanup lifecycle gap: cleanup_orphaned_agentic_workers consumes pid/started_at_s (agentic_workers.py:216-241) but blocking run_agentic_worker_fanout captures TimeoutExpired inline (status='timeout', no pid emitted), so producer-wired cleanup may have nothing to clean after a fan-out timeout.
- event_id 309561 `tdd_review`: Scope is allowed|required but no named test exercises the allowed policy (fan-out runs without hard-block on absence)
- event_id 309561 `tdd_review`: ISS-5 acceptance criterion 'over-budget specs rejected before launch' appears only in RED/GREEN prose, not as a named Test Case
- event_id 309561 `tdd_review`: No explicit fixtures/test-double inventory or location is specified
- event_id 309561 `tdd_review`: Durable-refs-preserved-for-failed-workers assertion (ISS-5) is in prose but not in the named timeout-cleanup case body
- event_id 309565 `tdd_review`: cursor_review_failed: missing dual_agent_outcome block
- event_id 309567 `tdd_review`: Scope is allowed|required but no named test exercises the allowed policy (fan-out runs without hard-block on absence)
- event_id 309567 `tdd_review`: ISS-5 acceptance criterion 'over-budget specs rejected before launch' appears only in RED/GREEN prose, not as a named Test Case
- event_id 309567 `tdd_review`: No explicit fixtures/test-double inventory or location is specified
- event_id 309567 `tdd_review`: Durable-refs-preserved-for-failed-workers assertion (ISS-5) is in prose but not in the named timeout-cleanup case body
