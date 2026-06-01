# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 317735 `prd_review`: both agents accepted
- event_id 317764 `issues_review`: Tests asserted GREEN by handoff but not re-executed here (test_status unknown)
- event_id 317764 `issues_review`: Phase 0 evidence under docs/dual-agent/reviewer-contract-reliability-20260531/ is still untracked and not yet committed
- event_id 317769 `issues_review`: both agents accepted
- event_id 317799 `tdd_review`: Test suite not executed by reviewer (uv run pytest permission-gated); test_status unknown until an operator/CI run confirms GREEN.
- event_id 317799 `tdd_review`: _run_litellm_structured is fully mocked in unit tests; the real OpenAI json_schema call path is covered only by the committed Phase 0 live probe.
- event_id 317799 `tdd_review`: Structured path omits request.timeout_s on the OpenAI client and assumes reviewer_max_tokens=4096 suffices for full critical_review JSON; truncation degrades safely to recoverable contract_unmet rather than fake-accept.
- event_id 317804 `tdd_review`: both agents accepted
- event_id 317910 `implementation_plan`: Core function _run_litellm_structured has no direct test coverage; a regression there would keep CI green while live review regresses to proceed_degraded, contradicting the slice's own intent.
- event_id 317910 `implementation_plan`: Could not independently re-run the test suite in this gate session; verdict relies on static review plus recorded receipts.
- event_id 317911 `implementation_plan`: agents have not both accepted yet; revise and continue
- event_id 317913 `implementation_plan`: Core function _run_litellm_structured has no direct test coverage; a regression there would keep CI green while live review regresses to proceed_degraded, contradicting the slice's own intent.
- event_id 317913 `implementation_plan`: Could not independently re-run the test suite in this gate session; verdict relies on static review plus recorded receipts.
- event_id 318054 `implementation_plan`: Test receipt unverified: pytest required approval that was not granted, so test_status is unknown rather than passed.
- event_id 318054 `implementation_plan`: Two TDD-named tests (test_reviewer_route_metadata_is_recorded, unit-level test_cursor_sdk_output_mode_routes_to_cursor_sdk_runner) were renamed or folded into other tests; coverage exists but exact traceability drifts from the TDD plan.
- event_id 318062 `implementation_plan`: both agents accepted
- event_id 318137 `execution`: test-evidence.md diff receipt sha is the placeholder PENDING_REFRESH_AFTER_EVIDENCE_UPDATE and should be refreshed to a real shasum
- event_id 318137 `execution`: Test pass status (539 passed) relies on committed receipts plus code inspection; not independently re-executed in this gate due to approval gating
- event_id 318138 `execution`: both agents accepted
- event_id 318198 `outcome_review`: Tests were not executed; accept is conditioned on the supervisor running the suite green before merge.
- event_id 318211 `outcome_review`: workflow_claim_verification_failed
- event_id 318213 `outcome_review`: Tests were not executed; accept is conditioned on the supervisor running the suite green before merge.
- event_id 318327 `outcome_review`: Independent test execution could not be performed in this session (uv run pytest permission-blocked); accepting on the self-reported 539-passed receipt repeats the prior workflow_claim_verification_failed.
- event_id 318328 `outcome_review`: agents have not both accepted yet; revise and continue
- event_id 318330 `outcome_review`: Independent test execution could not be performed in this session (uv run pytest permission-blocked); accepting on the self-reported 539-passed receipt repeats the prior workflow_claim_verification_failed.
- event_id 318392 `outcome_review`: Could not run pytest to confirm green receipts; accept is conditional on operator executing tests/test_cursor_agent.py and tests/test_dual_agent_workflow_driver.py.
- event_id 318405 `outcome_review`: both agents accepted
