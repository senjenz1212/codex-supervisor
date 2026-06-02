# Test Evidence: Reviewer Route Access Fix

## Final Local Verification

- `uv run --extra dev pytest tests/test_cursor_agent.py tests/test_dual_agent_workflow_driver.py -q`
  - Result: `114 passed in 69.54s`
- `uv run --extra dev pytest -q`
  - Result: `603 passed in 82.02s`
  - Final verification rerun before commit: `603 passed in 77.85s`
- `git diff --check`
  - Result: passed with no whitespace errors

## Supervised Workflow Verification

- Command:
  `TELEGRAM_BOT_TOKEN=fake TELEGRAM_CHAT_ID=fake uv run codex-supervisor-workflow --config /Users/sam.zhang/.codex-supervisor/config.yaml --request docs/dual-agent/reviewer-route-access-fix-20260601/workflow-request-cli.json --output docs/dual-agent/reviewer-route-access-fix-20260601/workflow-result-cli.json --fail-on-blocked`
- Result: accepted.
- Accepted gates: `prd_review`, `issues_review`, `tdd_review`, `implementation_plan`, `execution`, `outcome_review`.
- Independent reviewer evidence: `tdd_review`, `implementation_plan`, and `outcome_review` counted real `litellm_structured` fallback verdicts with `fallback_from_runtime=cursor_sdk`; outcome review used model `gemini-3.1-pro-preview`, `finish_reason=stop`, and `reviewer_runtime=litellm_structured`.
- Degraded recovery: not used for the accepted reviewer verdicts.

## Block Recovered Through Gate

The first supervised run blocked at `tdd_review` because the fallback reviewer
was incorrectly carrying Cursor's `composer-2.5` model into the LiteLLM route,
which produced a 403. The implementation was corrected so fallback from
`cursor_sdk` clears the Cursor model and selects the structured reviewer default
`gemini-3.1-pro-preview`; the same workflow was then resumed and accepted
through all remaining gates.
