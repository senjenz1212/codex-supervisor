# Implementation Plan

## Intent

Run hash-pinned AutoResearch evaluators as durable budget-enforced jobs and ship a replay-corpus reference evaluator as the default metric source. The implementation preserves AutoResearch as report-only: it can emit evidence, validation records, and proposals, but it cannot mutate policy or advance supervisor gates.

## Files / Modules To Touch

- `supervisor/autoresearch/orchestrator.py`: replace the direct evaluator call with a durable evaluator job adapter and persist the job metadata in attempt execution events.
- `supervisor/autoresearch/durable_jobs.py`: add the AutoResearch job adapter that reserves, claims, writes request artifacts, records spawned/terminal phases, and reattaches by idempotency token.
- `supervisor/autoresearch/evaluator.py`: persist per-trial progress, reload completed trials on retry, enforce `budget_usd` and `timeout_s`, and keep final evaluator run artifacts hashable.
- `supervisor/autoresearch/evaluators/replay_corpus.py`: provide the shipped replay-corpus evaluator that computes pass-rate metrics from the pinned agentic evaluation corpus.
- `supervisor/autoresearch/validation.py`: map budget and timeout execution failures into gaming flags while preserving existing fixture, hash, mutable-path, and report-only checks.
- `tests/test_autoresearch.py`: add public-boundary tests for durable job dispatch, crash resume, limit rejection, replay-corpus default metrics, and report-only invariants.

## Steps

1. Add the durable evaluator adapter and call it from `run_autoresearch_fixture(..., execution_mode="live")`.
2. Use a deterministic idempotency token `autoresearch:<run_id>:<experiment_id>:<attempt_id>` so a submit/drop/retry reattaches to the same evaluator job row.
3. Persist request and result artifacts under `evaluator-jobs/<attempt_id>/`, and keep per-trial progress under `evaluator-runs/<attempt_id>.progress.json`.
4. Preserve completed trial records during a retry, execute only missing trial indexes, and store the final evaluator artifact under `evaluator-runs/<attempt_id>.json`.
5. Resolve the default evaluator only when both `evaluator_ref` and `evaluator_hash` are empty, hash-pin the local replay-corpus evaluator, and report `pass_rate` as the metric.
6. Run focused AutoResearch/replay/corpus tests, run the full suite, then submit through the durable supervisor workflow with Cursor SDK rigorous review enabled.

## Traceability

- P1 maps to `test_autoresearch_live_evaluator_executes_through_durable_job_row`.
- P2 maps to `test_autoresearch_durable_evaluator_resumes_after_midrun_crash`.
- P3 maps to `test_autoresearch_live_evaluator_budget_overrun_is_flagged_and_rejected`, `test_autoresearch_live_evaluator_timeout_is_flagged_and_rejected`, and `test_autoresearch_live_evaluator_partial_progress_timeout_is_terminal`.
- P4 maps to `test_autoresearch_default_replay_corpus_evaluator_produces_pass_rate`.
- P5 maps to `test_autoresearch_report_only_invariants_remain_false_for_live_run`.

## Risks

- The generic workflow dispatcher is built around `mcp_tools.codex_supervisor_workflow_cli`, so the AutoResearch adapter must reuse the ledger lane and claim primitives without pretending the workflow CLI can execute evaluator scripts directly.
- Timeout and crash handling must not retry forever; retryable non-timeout crashes keep completed trial progress, while budget and timeout limit failures become terminal failed job outcomes with validation flags.
- The replay-corpus evaluator must remain deterministic and local-only so a default experiment does not accidentally spend model budget or mutate policy.
- Source-checkout mutation detection can produce conservative failures in a busy development worktree, so tests should prove restoration and report-only rejection rather than weakening the trust boundary.

## Validation

- Run `.venv/bin/python -m pytest tests/test_autoresearch.py tests/test_autoresearch_policy_evolution.py tests/test_agentic_eval_corpus.py tests/test_replay_cli.py -q`.
- Run `.venv/bin/python -m pytest -q`.
- Run `git diff --check`.
- Submit the completed slice through `submit_dual_agent_workflow_job` with PRD/TDD receipts, `cursor_review=true`, `cursor_review_profile=rigorous`, and `reviewer_output_mode=cursor_sdk`.
