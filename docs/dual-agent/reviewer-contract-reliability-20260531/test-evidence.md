# Reviewer Contract Reliability Test Evidence

## Diff Receipt

- Command: `git diff | shasum -a 256`
- Result: `76fc42d1b4004a2abae87f66ab637db945e0e4b58be0130edac4d07646e5aadb`
- Changed files:
  - `config.example.yaml`
  - `mcp_tools/codex_supervisor_stdio.py`
  - `mcp_tools/codex_supervisor_workflow_cli.py`
  - `supervisor/config.py`
  - `supervisor/cursor_agent.py`
  - `tests/test_cursor_agent.py`
  - `tests/test_dual_agent_workflow_driver.py`

## Focused Test Receipts

- Command: `uv run pytest tests/test_cursor_agent.py -q`
- Result: `18 passed in 0.76s`

- Command: `uv run pytest tests/test_cursor_agent.py tests/test_dual_agent_workflow_driver.py::test_workflow_kwargs_from_payload_preserves_dynamic_workflow_preview_fields tests/test_dual_agent_workflow_driver.py::test_workflow_invokes_reviewer_after_claude_accept_with_non_claude_default tests/test_dual_agent_workflow_driver.py::test_cursor_sdk_output_mode_routes_legacy_model_to_reviewer_request -q`
- Result: `21 passed in 3.97s`

- Command: `uv run pytest tests/test_cursor_agent.py tests/test_dual_agent_workflow_driver.py tests/test_codex_supervisor_mcp_stdio.py tests/test_target_config_load.py -q`
- Result: `99 passed in 66.25s`

## Full Suite Receipt

- Command: `uv run --extra dev pytest -q`
- Result: `539 passed in 71.79s`

## Acceptance Mapping

- Config/default route: `test_select_reviewer_defaults_to_phase0_structured_gemini`
- CLI payload threading: `test_workflow_kwargs_from_payload_preserves_dynamic_workflow_preview_fields`
- Direct structured gateway call: `test_run_litellm_structured_calls_openai_schema_gateway`
- Structured happy path: `test_structured_litellm_reviewer_returns_fidelity_passing_outcome`
- Cursor SDK compatibility: `test_cursor_sdk_output_mode_routes_legacy_model_to_reviewer_request`
- Read-only guard: `test_structured_litellm_reviewer_preserves_read_only_guard`
- Completeness enforcement: `test_structured_litellm_reviewer_enforces_contract_completeness`
- Real rejection blocking: `test_structured_litellm_reviewer_revise_blocks_workflow`, `test_structured_litellm_reviewer_deny_blocks_workflow`
- Infrastructure and contract classification: `test_structured_litellm_failure_classifies_as_infrastructure_unavailable`, `test_structured_litellm_invalid_json_classifies_as_contract_unmet`, `test_structured_litellm_truncation_classifies_as_contract_unmet`
- Downstream independent reviewer metadata: `test_workflow_invokes_reviewer_after_claude_accept_with_non_claude_default`
