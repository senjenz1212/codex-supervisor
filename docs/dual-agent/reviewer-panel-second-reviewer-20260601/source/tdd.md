# TDD Plan: Reviewer Panel Second Reviewer

## test_reviewer_registry_returns_codex_cli_second_reviewer

Maps to: P1, P2

RED: `configured_reviewers(...)` returns only the existing reviewer slot.

GREEN: The registry returns reviewer 0 plus reviewer 1. Reviewer 1 has runtime
`codex_cli`, provider family `openai`, model `gpt-5.5`, codebase tool access,
and an agentic-capable spec because the adapter records tool-backed transcript
and hashes when it runs.

## test_codex_cli_reviewer_parses_typed_outcome_with_hashes

Maps to: P2, P5

RED: There is no production adapter that can turn Codex CLI JSONL into a
`CursorInvocationResult`-compatible reviewer result.

GREEN: A fake Codex runner returns JSONL with a command read and an agent
message containing `<dual_agent_outcome>`. The adapter parses the outcome,
returns `reviewer_runtime=codex_cli`, `reviewer_assurance=tool_backed_primary`,
and exposes transcript metadata that downstream result mapping hashes.

## test_workflow_exposes_independent_reviewer_results_and_dual_writes_events

Maps to: P1, P2, P3

RED: A workflow with `cursor_review=true` invokes only reviewer 0 and emits one
panel result.

GREEN: The workflow invokes both configured reviewers, writes both results into
`independent_reviewer_results[]`, records different provider families, and the
panel decision accepts when both accept.

## test_second_reviewer_important_revise_blocks

Maps to: P3

RED: Reviewer 1 can return a real important revise while the workflow still
advances because only reviewer 0 feeds the conservative evaluator.

GREEN: Reviewer 1's important revise is included in the panel decision and
blocks exactly like reviewer 0's important revise.

## test_second_reviewer_outage_proceeds_only_degraded

Maps to: P4

RED: A recoverable outage from reviewer 1 either blocks without recovery context
or is silently counted as accept.

GREEN: With `reviewer_unavailable_policy=proceed_degraded`, reviewer 0 and
Claude/Codex accept, and reviewer 1 unavailable, the workflow proceeds through
degraded recovery, records the unavailable reviewer, and preserves the missing
verdict in panel metadata.

## Regression Commands

- `uv run pytest tests/test_dual_agent_workflow_driver.py::test_reviewer_registry_returns_codex_cli_second_reviewer tests/test_dual_agent_workflow_driver.py::test_codex_cli_reviewer_parses_typed_outcome_with_hashes tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_records_two_independent_reviewers tests/test_dual_agent_workflow_driver.py::test_second_reviewer_important_revise_blocks tests/test_dual_agent_workflow_driver.py::test_second_reviewer_outage_proceeds_only_degraded -q`
- `uv run pytest tests/test_dual_agent_workflow_driver.py -q`
- `uv run --extra dev pytest -q`
