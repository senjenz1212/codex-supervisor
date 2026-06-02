# TDD Plan: Reviewer Panel Eval Runner

Task id: `reviewer-panel-eval-runner-20260601`

Public-boundary rule: the first RED test for every PRD promise must exercise
`reviewer_panel_eval_runner`, except the distinction guard for the existing
lead-mode report boundary, which also exercises `agentic_eval_report`.

External reviewers, model APIs, Telegram, Cursor, Codex, and Claude must be
faked or replayed below the runner boundary. Tests must not mock away runner
routing, panel iteration, label handling, metric reduction, export, or policy
non-mutation.

## test_reviewer_panel_eval_runner_validates_labeled_fixture_schema

Maps to: P1, P4, P5, P6, Slice 1

Public boundary: `reviewer_panel_eval_runner`

Forbidden outcomes under test: ambiguous labels, live default execution,
lead-mode schema reuse.

RED: Call the runner with one valid fixture set and one invalid label. The test
expects explicit fixture schema versioning, label validation, default
`execution_mode=fixture_replay`, and a reviewer-panel eval schema distinct from
`agentic-lead-eval/v1`. This fails before the boundary exists.

GREEN: Add the runner entrypoint, fixture schema parser, accepted label enum,
and report metadata stub. Do not compute final metrics yet.

## test_reviewer_panel_eval_runner_records_all_reviewer_rows

Maps to: P1, P4, Slice 2

Public boundary: `reviewer_panel_eval_runner`

Forbidden outcomes under test: evaluating only reviewer 0, deriving rows from
aggregate panel decision, hiding missing reviewers, live calls by default.

RED: Run a fixture with two reviewer slots and three labeled tasks. Assert six
rows sorted by task id and reviewer id, including one explicit unavailable row
with cassette/input/output hash metadata. This fails before panel iteration and
row normalization exist.

GREEN: Wire the runner to iterate the configured reviewer roster using cassette
adapters below the boundary. Normalize each result into a row with reviewer id,
task id, gate, label, decision, severity, confidence, verdict-present flag,
runtime/model/provider metadata, transcript refs, output hash, cost, and
latency.

## test_reviewer_panel_eval_runner_computes_per_reviewer_metrics

Maps to: P2, Slice 3

Public boundary: `reviewer_panel_eval_runner`

Forbidden outcomes under test: missing verdict counted as accept, rates without
counts, cost/latency omitted, false-block causes hidden.

RED: Run a labeled fixture where reviewer 0 has one false accept and reviewer 1
has one false block caused by an unavailable verdict. Assert per-reviewer task
count, verdict-present count, accept/revise/deny/missing counts, false-accept
rate, false-block rate, false-block cause breakdown, unavailable rate, total
and average cost, and total and average latency. This fails before per-reviewer
metrics are wired into the report.

GREEN: Add the per-reviewer metric reducer and attach summaries to the runner
report. Keep raw denominators next to every rate.

## test_reviewer_panel_eval_runner_computes_pairwise_dependency_metrics

Maps to: P3, Slice 4

Public boundary: `reviewer_panel_eval_runner`

Forbidden outcomes under test: pairwise metrics from aggregate panel decision,
numeric correlation on zero variance, prose-only independence claims, hidden
failure overlap.

RED: Run a fixture with two reviewers that includes agreement, disagreement,
one overlapping false block, and one zero-variance subcase. Assert comparable
task count, agreement rate, disagreement counts, false-accept overlap,
false-block overlap, combined failure Jaccard, raw contingency tables, phi
correlation where variance exists, and `not_applicable` status where variance
is zero. This fails before pairwise metrics exist.

GREEN: Add pairwise reducers over per-reviewer rows. Compute phi only from raw
binary vectors with variance; otherwise emit a reasoned not-applicable status.

## test_reviewer_panel_eval_runner_exports_replay_and_ledger_artifacts

Maps to: P4, P5, Slice 5

Public boundary: `reviewer_panel_eval_runner`

Forbidden outcomes under test: artifacts without hashes, omitted ledger refs,
policy mutation, active weight emission.

RED: Run the fixture eval with an output directory and fake state ledger. Assert
report JSON, markdown report, raw rows, replay manifest, labeled-set hash,
cassette ids, reviewer roster, report hash, and ledger event ids are exported.
Assert `policy_change_allowed=false` and no active weights/config changes are
emitted. This fails before export and ledger wiring exist.

GREEN: Add export writing and eval-only ledger events. Keep events clearly
classified as observations, not gate decisions.

## test_reviewer_panel_eval_runner_is_distinct_from_agentic_eval_report

Maps to: P6, Slice 5

Public boundary: `agentic_eval_report` and `reviewer_panel_eval_runner`

Forbidden outcomes under test: lead-mode report executes reviewers, reviewer
panel report uses lead-mode schema, lead rows require reviewer fields.

RED: Run the existing lead-mode report fixture and the new reviewer-panel eval
fixture in the same test module. Assert `build_agentic_eval_report(rows)` still
returns `schema_version=agentic-lead-eval/v1`, `default_change_allowed=false`,
and no reviewer-panel rows. Assert the new runner returns its own schema and
runner metadata. This fails if the two paths are conflated.

GREEN: Keep `supervisor.agentic_eval` focused on lead-mode aggregation and
place reviewer-panel eval under a separate runner/report module.

## Regression Commands

- `uv run pytest tests/test_reviewer_panel_eval_runner.py -q`
- `uv run pytest tests/test_agentic_eval.py tests/test_dual_agent_workflow_driver.py::test_workflow_exposes_independent_reviewer_results_and_dual_writes_events tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_panel_blocks_important_reviewer_revise tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_panel_missing_verdict_does_not_accept -q`
- `uv run --extra dev pytest -q`

## Translation Audit

- Every PRD promise has at least one issue claimant.
- Every issue has a `PRD promise` block with public boundary and forbidden
  outcomes.
- Every first RED test names `reviewer_panel_eval_runner` or the existing
  `agentic_eval_report` distinction boundary.
- Mocks and cassettes sit below the runner boundary and do not replace runner
  routing, panel iteration, metrics, exports, or policy non-mutation.
- Forbidden outcomes from the PRD are represented in TDD assertions.
