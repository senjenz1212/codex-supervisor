# Test Evidence: Reviewer Panel Second Reviewer

## Route Evidence

- Cursor SDK route evidence remains in `route-evidence/cursor-sdk-probe.json`: both `composer-2.5` and `gpt-5.5` returned `reviewer_infrastructure_unavailable` / `internal: internal error` and no typed verdict.
- Codex CLI route evidence remains in `route-evidence/codex-cli-readonly-probe.jsonl` and `route-evidence/codex-cli-readonly-probe-summary.json`: the `gpt-5.5` Codex CLI reviewer returned a typed verdict with command-execution evidence and a transcript hash.

## Validation Commands

- `python3 -m py_compile supervisor/reviewer_registry.py supervisor/cursor_agent.py mcp_tools/codex_supervisor_stdio.py tests/test_dual_agent_workflow_driver.py` -> passed.
- `uv run pytest tests/test_dual_agent_workflow_driver.py::test_reviewer_registry_returns_codex_cli_second_reviewer tests/test_dual_agent_workflow_driver.py::test_codex_cli_reviewer_parses_typed_outcome_with_hashes tests/test_dual_agent_workflow_driver.py::test_codex_cli_reviewer_without_command_evidence_is_not_agentic tests/test_dual_agent_workflow_driver.py::test_codex_cli_reviewer_parses_session_event_jsonl tests/test_dual_agent_workflow_driver.py::test_workflow_exposes_independent_reviewer_results_and_dual_writes_events tests/test_dual_agent_workflow_driver.py::test_second_reviewer_important_revise_blocks tests/test_dual_agent_workflow_driver.py::test_second_reviewer_outage_proceeds_only_degraded -q` -> 7 passed.
- `uv run pytest tests/test_dual_agent_workflow_driver.py -q` -> 94 passed.
- `uv run --extra dev pytest -q` -> 619 passed.

## Acceptance Mapping

- Two reviewer results: covered by `test_workflow_exposes_independent_reviewer_results_and_dual_writes_events`.
- Distinct lineage and truthful assurance: covered by `test_reviewer_registry_returns_codex_cli_second_reviewer`, `test_codex_cli_reviewer_parses_typed_outcome_with_hashes`, and `test_codex_cli_reviewer_without_command_evidence_is_not_agentic`.
- Conservative aggregation across both reviewers: covered by `test_second_reviewer_important_revise_blocks`.
- Honest single-reviewer outage recovery: covered by `test_second_reviewer_outage_proceeds_only_degraded`.
- Deterministic tests: all workflow tests inject fake Codex CLI and Cursor-compatible reviewers; live route proof stays in route evidence artifacts.
