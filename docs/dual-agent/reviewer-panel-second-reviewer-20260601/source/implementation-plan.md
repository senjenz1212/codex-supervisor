# Implementation Plan: Reviewer Panel Second Reviewer

## Intent

Register a second working independent reviewer of a distinct lineage from the
Claude lead and the existing Gemini reviewer. The selected route is the
Codex CLI/GPT-family reviewer proven by route evidence. The implementation
must keep conservative panel semantics unchanged and preserve legacy
`cursor_review` compatibility.

## Files / Modules To Touch

- `supervisor/reviewer_registry.py`: add the Codex CLI reviewer adapter, route
  parser, provider/assurance metadata, and multi-result mapping helper.
- `mcp_tools/codex_supervisor_stdio.py`: invoke the full reviewer roster,
  evaluate the conservative panel over all reviewer results, and extend
  reviewer-unavailable recovery to panel-level failures.
- `tests/test_dual_agent_workflow_driver.py`: add deterministic fake Codex CLI
  reviewer fixtures plus public-boundary workflow tests for two accepts,
  second-reviewer revise, and second-reviewer outage.
- `docs/dual-agent/reviewer-panel-second-reviewer-20260601/`: retain PRD/TDD,
  route evidence, test evidence, workflow request/result, transcript, and replay
  artifacts for audit.

## Steps

1. Add a small `CodexCliReviewer` adapter behind the existing reviewer registry.
   It runs `codex exec --json --sandbox read-only`, parses agent-message
   `<dual_agent_outcome>` blocks, records command-execution evidence, and
   returns a `CursorInvocationResult`-compatible payload.
2. Update `configured_reviewers` to return reviewer 0 as the legacy
   Cursor-compatible slot and reviewer 1 as the Codex CLI/GPT-family reviewer.
3. Update the workflow review path to invoke every registry reviewer after
   Claude gate acceptance. Keep `cursor_review` anchored to reviewer 0 while
   writing all results to `independent_reviewer_results[]`.
4. Feed the full result list into the existing conservative panel evaluator.
   A real important/critical revise or deny from reviewer 1 must block exactly
   like reviewer 0.
5. Extend reviewer-unavailable recovery to detect recoverable infrastructure
   failures from any panel result. Proceed-degraded may advance only when
   Claude, Codex, and all available real reviewer verdicts accept.
6. Add deterministic tests with fake reviewer runners so normal test execution
   never calls live Codex or Cursor.
7. Run focused reviewer-panel tests, the workflow driver suite, and the full
   repository suite; export the supervised workflow artifacts.

## Risks

- The Codex CLI reviewer is OpenAI/GPT-family, which is distinct from Claude and
  Gemini but still correlated with the Codex supervisor role. The result must
  be labeled truthfully as `provider_family=openai`, not as a fully independent
  non-supervisor lineage.
- Live Codex/Cursor invocation in unit tests would make replay nondeterministic,
  so the test harness must inject fake runner fixtures by default.
- A panel-level outage path could accidentally count the missing reviewer as
  accept. The outage regression must assert degraded recovery metadata and the
  missing reviewer in the panel decision.
- Legacy artifact readers still depend on `cursor_review`; preserving reviewer 0
  as the compatibility payload avoids an export/replay break.

## Traceability

- P1 -> `test_workflow_exposes_independent_reviewer_results_and_dual_writes_events`
  and `test_reviewer_registry_returns_codex_cli_second_reviewer`.
- P2 -> `test_codex_cli_reviewer_parses_typed_outcome_with_hashes`.
- P3 -> `test_second_reviewer_important_revise_blocks`.
- P4 -> `test_second_reviewer_outage_proceeds_only_degraded`.
- P5 -> the focused reviewer-panel tests, the workflow driver suite, and the
  full repository suite recorded in `test-evidence.md`.
