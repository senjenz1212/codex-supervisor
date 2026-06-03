# PRD: Agentic Eval-Harness Runner

Task id: `agentic-eval-harness-runner-20260603`

## Problem Statement

`supervisor/agentic_eval.py` currently aggregates rows that somebody else has
already produced. That makes the fan-out policy decision too hand-wavy: an
operator can compare historical rows, but there is no deterministic runner that
loads a task corpus, executes or replays the three lead modes, enforces equal
compute budgets, and attaches concrete evidence to the resulting scores.

The user specifically needs a report-only evaluation path. The runner must make
future fan-out policy decisions empirical without changing the current default,
without calling external model services by accident, and without mixing eval scoring with
the production reviewer panel.

## Solution

Add `agentic_eval_runner(dataset_path=..., output_dir=...)` next to the existing
`build_agentic_eval_report(rows)` function. The runner loads a small YAML/JSON
task corpus, requires exactly `lead_direct`, `agentic_allowed`, and
`agentic_required` arms per task, validates equal total token and USD budgets
across those arms, replays workflow-shaped cassettes by default, scores required
verdicts through a deterministic evidence decision tree, and writes deterministic
report/evidence artifacts.

The default execution mode is `fixture_replay`. It rejects live workflow
execution unless a caller explicitly opts in, and replay rows must look like real
dual-agent workflow outcomes: full gate sequence, required P-probes, and
reviewer-panel decisions must be present before rows materialize.

## User Stories

1. As a supervisor operator, I can run one fixture-backed eval and see the
   three-arm comparison for each representative task.
2. As a policy owner, I can trust the comparison because every arm receives the
   same total token and USD budget for a given task.
3. As a maintainer, I can run the eval in CI without accidentally contacting
   live Telegram, agent, subprocess, Anthropic, OpenAI, or Cursor surfaces.
4. As a reviewer, I can inspect the evidence behind every required verdict
   rather than trusting a holistic score.
5. As a release owner, I can consume the report knowing it never flips
   `agentic_lead_policy` or authorizes a default change by itself.

## PRD Promise Contracts

P1. three-arm-gated-comparison

- User-visible promise: Given a dataset path, the runner produces exactly one
  row per required mode for every task: `lead_direct`, `agentic_allowed`, and
  `agentic_required`.
- Representative action: Call `agentic_eval_runner(dataset_path=fixture_path)`.
- Public boundary: `supervisor.agentic_eval.agentic_eval_runner`.
- Allowed outcomes: rows preserve workflow status, gate status, P-probe status,
  reviewer-panel decisions, and feed `build_agentic_eval_report`.
- Forbidden outcomes: missing an arm, adding an extra mode, or comparing helper
  assertions that do not resemble gated workflow outcomes.
- Related user stories: 1, 4.

P2. equal-compute-budget

- User-visible promise: Each task compares modes under the same total token and
  USD budget.
- Representative action: Run an equal-budget fixture and an intentionally
  unequal fixture.
- Public boundary: `agentic_eval_runner`.
- Allowed outcomes: equal budget tuples are recorded in each row; unequal
  budgets abort before rows or artifacts are produced.
- Forbidden outcomes: fan-out arms receiving extra aggregate budget, silent
  normalization after mismatch, or per-worker budget comparisons masquerading as
  total budget equality.
- Related user stories: 2.

P3. replay-only-default

- User-visible promise: Default evaluation never calls live workflows, target
  agents, subprocesses, Telegram, Anthropic, OpenAI, or Cursor.
- Representative action: Run default fixture replay while passing a workflow
  runner sentinel that would raise if invoked.
- Public boundary: `agentic_eval_runner`.
- Allowed outcomes: cassette/replay-backed rows; non-fixture execution requires
  explicit live opt-in.
- Forbidden outcomes: network/provider/subprocess calls in default mode, hidden
  live execution behind report generation, or changing production workflow
  behavior to satisfy the eval.
- Related user stories: 3.

P4. evidence-backed-deterministic-scoring

- User-visible promise: Every required verdict is scored by fixed evidence rules
  and carries concrete evidence.
- Representative action: Score one arm twice with identical evidence and score
  one arm after removing evidence for a required verdict.
- Public boundary: `score_agentic_eval_arm` and `agentic_eval_runner`.
- Allowed outcomes: concrete probe receipts, artifact paths, or diff hunks
  support every passed verdict; missing or non-passing evidence fails that
  verdict; identical input produces identical scores.
- Forbidden outcomes: holistic model judgment, unevidenced verdict credit, or
  non-deterministic scoring from timestamps or process state.
- Related user stories: 4.

P5. report-only-invariant

- User-visible promise: The runner emits a comparison report and evidence pack
  only; it does not enable fan-out or mutate defaults.
- Representative action: Run the fixture with an output directory and inspect
  the exported report.
- Public boundary: `agentic_eval_runner` and `build_agentic_eval_report`.
- Allowed outcomes: report JSON, evidence JSON, rows JSONL, replay manifest,
  `default_change_allowed=false`, and an `agentic_lead_policy` snapshot of
  `off`.
- Forbidden outcomes: writing config, flipping `agentic_lead_policy`, changing
  worker caps, or authorizing a default policy change without operator review.
- Related user stories: 5.

## Implementation Decisions

- Keep `build_agentic_eval_report(rows)` as the aggregation function and extend
  it only with score and graceful-degradation totals.
- Add `agentic_eval_runner`, `load_agentic_eval_dataset`, and
  `score_agentic_eval_arm` in `supervisor/agentic_eval.py`.
- Use YAML or JSON fixture datasets; include the small deterministic fixture
  corpus under `tests/fixtures/agentic_eval/`.
- Treat `fixture_replay` as the default execution mode and require
  `allow_live_calls=True` for non-fixture execution.
- Validate replay cassettes for the workflow gate sequence, P1/P2/P3 probes,
  P13/P14 probes on agentic arms, and reviewer-panel decisions.
- Export sorted ASCII JSON/JSONL artifacts with stable SHA-256 hashes.

## Testing Decisions

- The first RED test exercises `agentic_eval_runner(dataset_path=...)` rather
  than only helper functions.
- Unit tests cover required mode coverage, equal-budget rejection, replay-shape
  rejection, deterministic scoring, missing-evidence failure, report-only
  export behavior, and replay no-live-call guard.
- Regression coverage includes `tests/test_agentic_executor.py` and
  `tests/test_dual_agent_workflow_driver.py` to prove the dormant fan-out and
  workflow paths are not weakened.
- Full-suite verification is required before the supervised outcome review.

## Out Of Scope

- Do not enable fan-out or change `agentic_lead_policy`.
- Do not change the production reviewer panel or calibrated aggregation.
- Do not modify `supervisor/state.py`, scaling, admission control, Postgres, or
  single-writer architecture.
- Do not raise `AGENTIC_WORKER_MAX_SUBAGENTS`.
- Do not call external model services or target agents from the default eval path.
