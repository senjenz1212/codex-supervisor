# Test Evidence

## 2026-05-31 Post-Implementation

| Check | Result | Notes |
|---|---:|---|
| `uv run pytest tests/test_cursor_agent.py -q` | pass | 25 passed in 2.17s |
| `uv run pytest tests/test_dual_agent_workflow_driver.py -q` | pass | 55 passed in 61.23s |
| `uv run pytest tests/test_agent_mailbox.py tests/test_dual_agent_artifacts.py tests/test_failure_taxonomy.py -q` | pass | 38 passed in 1.16s |
| `git diff --check` | pass | no whitespace errors |
| `uv run --extra dev pytest -q` | pass | 547 passed in 73.67s |
| `uv run python scripts/probe_cursor_sdk_live.py --output-dir docs/dual-agent/independent-reviewer-cursor-primary-20260531/live-cursor-sdk-probe-postpatch --timeout-s 45` | pass | Cursor SDK loaded `CURSOR_API_KEY` from Codex MCP env and returned `cursor_review_ok` in 11.742s |
| `uv run python scripts/probe_cursor_sdk_live.py --output-dir docs/dual-agent/independent-reviewer-cursor-primary-20260531/live-cursor-sdk-probe-postpatch-2 --timeout-s 45` | pass | Cursor SDK loaded `CURSOR_API_KEY` from Codex MCP env and returned `cursor_review_ok` in 8.399s |

## 2026-05-31 Final Verification

| Check | Result | Notes |
|---|---:|---|
| `uv run pytest tests/test_cursor_agent.py -q` | pass | 26 passed in 2.56s; adds explicit coverage that Cursor contract misses do not fall back to LiteLLM/Gemini even with an explicit fallback key |
| `uv run pytest tests/test_dual_agent_workflow_driver.py -q` | pass | 55 passed in 67.99s |
| `uv run --extra dev pytest -q` | pass | 549 passed in 73.91s; includes recovered-triage exporter regression |
| `uv run python scripts/probe_cursor_sdk_live.py --output-dir docs/dual-agent/independent-reviewer-cursor-primary-20260531/live-cursor-sdk-probe-current --timeout-s 45` | pass | Cursor SDK loaded `CURSOR_API_KEY` from Codex MCP env and returned `cursor_review_ok` in 9.506s |

## Live Cursor SDK Probe

- Artifact: `docs/dual-agent/independent-reviewer-cursor-primary-20260531/live-cursor-sdk-probe-postpatch/summary.json`
- Artifact: `docs/dual-agent/independent-reviewer-cursor-primary-20260531/live-cursor-sdk-probe-postpatch-2/summary.json`
- Artifact: `docs/dual-agent/independent-reviewer-cursor-primary-20260531/live-cursor-sdk-probe-current/summary.json`
- Runtime: `cursor_sdk`
- Output mode: `cursor_sdk`
- Assurance: `tool_backed_primary`
- Model: `composer-2.5`
- Prompt chars: `1666`
- Fallback: none

## Reviewer Gate Follow-Up

The first postpatch supervised rerun blocked at `tdd_review` because Cursor
found environment-sensitive fallback behavior: ambient `OPENAI_API_KEY` could
activate structured fallback during tests. The implementation now requires an
explicit request/config `openai_api_key` for fallback, keeps post-retry contract
misses as `reviewer_contract_unmet`, and adds deterministic tests for ambient
env isolation, positive Codex MCP Cursor key loading, explicit LiteLLM
lower-assurance labeling, and both-runtimes-fail recovery.
