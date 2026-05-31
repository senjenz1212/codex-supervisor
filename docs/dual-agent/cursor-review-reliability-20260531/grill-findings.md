# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 309847 `prd_review`: P4 recovery path wording ('retries/falls back or escalates') is wider than the intent; infra-classified misses must stay blocked as typed/durable artifact, never auto-advance
- event_id 309847 `prd_review`: reviewer_contract_unmet vs reviewer_infrastructure_unavailable trigger boundary is undefined in the PRD
- event_id 309847 `prd_review`: Bounded retry count unspecified in PRD (handoff policy implies retry_once); TDD needs concrete bound
- event_id 309847 `prd_review`: PRD-local P1-P5 labels collide with system probes P1/P2/P3/P13/P14 named in non-goals (clarity only)
- event_id 309848 `prd_review`: both agents accepted
- event_id 309873 `issues_review`: ISS-3 'proceeds only when explicitly permitted' is the highest-risk surface; must be proven in TDD that the proceed path cannot silently advance a gate on a missing verdict (mitigated by ISS-2/ISS-3 no-accept criteria and non-goals)
- event_id 309874 `issues_review`: both agents accepted
- event_id 309904 `tdd_review`: Valid-accept happy path is only a sub-assertion of test_valid_cursor_revise_still_blocks_after_retry_hardening, not a standalone regression, despite intent promising valid typed outcome behaves normally.
- event_id 309904 `tdd_review`: Durability test framing is loose about where the simulated Transport-closed drop occurs; test must re-read persisted classification independent of any live Cursor call.
- event_id 309904 `tdd_review`: First RED requires introducing an injectable Cursor-runner seam since _run_cursor_sdk hard-imports cursor_sdk (cursor_agent.py:165) and only status_runner is injectable today.
- event_id 309904 `tdd_review`: No targeted P13/P14 regression test; non-goal protection relies on suite-green only.
- event_id 309908 `tdd_review`: cursor_review_failed: missing dual_agent_outcome block
- event_id 309910 `tdd_review`: Valid-accept happy path is only a sub-assertion of test_valid_cursor_revise_still_blocks_after_retry_hardening, not a standalone regression, despite intent promising valid typed outcome behaves normally.
- event_id 309910 `tdd_review`: Durability test framing is loose about where the simulated Transport-closed drop occurs; test must re-read persisted classification independent of any live Cursor call.
- event_id 309910 `tdd_review`: First RED requires introducing an injectable Cursor-runner seam since _run_cursor_sdk hard-imports cursor_sdk (cursor_agent.py:165) and only status_runner is injectable today.
- event_id 309910 `tdd_review`: No targeted P13/P14 regression test; non-goal protection relies on suite-green only.
- event_id 309971 `tdd_review`: ISS-2 conflates reviewer_infrastructure_unavailable (test name) with reviewer_contract_unmet (body); the contract-miss case should be asserted distinctly from SDK-missing/transport
- event_id 309971 `tdd_review`: No test explicitly asserts the retry-count upper bound; bounded-retry metadata promised by grill finding 5 should have an explicit ceiling assertion
- event_id 309971 `tdd_review`: _run_cursor_sdk is not currently injectable; GREEN must add a runner seam so the fake-SDK RED is honest
- event_id 309979 `tdd_review`: cursor_review_failed: missing dual_agent_outcome block
- event_id 309981 `tdd_review`: ISS-2 conflates reviewer_infrastructure_unavailable (test name) with reviewer_contract_unmet (body); the contract-miss case should be asserted distinctly from SDK-missing/transport
- event_id 309981 `tdd_review`: No test explicitly asserts the retry-count upper bound; bounded-retry metadata promised by grill finding 5 should have an explicit ceiling assertion
- event_id 309981 `tdd_review`: _run_cursor_sdk is not currently injectable; GREEN must add a runner seam so the fake-SDK RED is honest
- event_id 310009 `tdd_review`: Test 2 names only reviewer_contract_unmet; distinguish reviewer_infrastructure_unavailable (SDK/transport exceptions, no retry) from reviewer_contract_unmet (SDK ran, no parseable outcome, retried).
- event_id 310009 `tdd_review`: Test 3 should explicitly assert default recovery is block/escalate, not auto-advance, to prove no gate bypass.
- event_id 310009 `tdd_review`: Durability test should pin where the transport failure is injected (after persist, on read return) to prove write-before-export ordering rather than just persisted-state presence.
- event_id 310013 `tdd_review`: cursor_review_failed: missing dual_agent_outcome block
- event_id 310015 `tdd_review`: Test 2 names only reviewer_contract_unmet; distinguish reviewer_infrastructure_unavailable (SDK/transport exceptions, no retry) from reviewer_contract_unmet (SDK ran, no parseable outcome, retried).
- event_id 310015 `tdd_review`: Test 3 should explicitly assert default recovery is block/escalate, not auto-advance, to prove no gate bypass.
- event_id 310015 `tdd_review`: Durability test should pin where the transport failure is injected (after persist, on read return) to prove write-before-export ordering rather than just persisted-state presence.
- event_id 310053 `tdd_review`: Malformed-but-present dual_agent_outcome block (present but invalid JSON / missing required fields) is not a named test, though P1/ISS-1 promise retry for malformed not just missing
- event_id 310053 `tdd_review`: P4 recovery branch (proceed-when-permitted vs escalate-deterministically) is not explicitly asserted; only not-counted-as-accept is covered
- event_id 310053 `tdd_review`: Valid-accept-advances path is folded into the revise regression rather than a standalone named test per ISS-5
- event_id 310057 `tdd_review`: cursor_review_failed: missing dual_agent_outcome block
- event_id 310059 `tdd_review`: Malformed-but-present dual_agent_outcome block (present but invalid JSON / missing required fields) is not a named test, though P1/ISS-1 promise retry for malformed not just missing
- event_id 310059 `tdd_review`: P4 recovery branch (proceed-when-permitted vs escalate-deterministically) is not explicitly asserted; only not-counted-as-accept is covered
- event_id 310059 `tdd_review`: Valid-accept-advances path is folded into the revise regression rather than a standalone named test per ISS-5
- event_id 310086 `tdd_review`: Bounded-retry count is under-specified ('bounded metadata'); handoff policy says retry_once_with_corrective_packet, so the retry test should assert that concrete numeric bound
- event_id 310086 `tdd_review`: P5 durability test describes transport-failure injection vaguely; read_gate_transcript reads local SQLite ledger not live MCP, so test should assert verdict persisted to tri_agent_cursor_review before export rather than simulating MCP transport closure
- event_id 310090 `tdd_review`: cursor_review_failed: missing dual_agent_outcome block
- event_id 310092 `tdd_review`: Bounded-retry count is under-specified ('bounded metadata'); handoff policy says retry_once_with_corrective_packet, so the retry test should assert that concrete numeric bound
- event_id 310092 `tdd_review`: P5 durability test describes transport-failure injection vaguely; read_gate_transcript reads local SQLite ledger not live MCP, so test should assert verdict persisted to tri_agent_cursor_review before export rather than simulating MCP transport closure
