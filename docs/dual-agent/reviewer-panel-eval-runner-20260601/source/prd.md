# PRD: Reviewer Panel Eval Runner

Task id: `reviewer-panel-eval-runner-20260601`

## Problem Statement

The reviewer panel now has panel-shaped results, a second reviewer route, and a
conservative non-weighted panel decision, but the project still lacks the data
substrate required before calibrated weighting can be discussed. Operators need
a deterministic eval runner that replays labeled gate decisions through the
reviewer panel and measures where reviewers agree, disagree, fail together,
false-accept, false-block, spend money, and add latency.

The existing `supervisor/agentic_eval.py` function
`build_agentic_eval_report(rows)` is a lead-mode report aggregator. It compares
`lead_direct`, `agentic_allowed`, and `agentic_required` rows and never executes
reviewers. It is not the reviewer-panel eval runner requested here and must not
be extended in a way that conflates the lead-mode/fan-out eval track with panel
dependency measurement.

## Solution

Add a reports-only reviewer-panel eval runner. The runner accepts a labeled
task set, deterministic reviewer cassettes or fixtures, and the configured
reviewer panel. It replays each labeled gate decision through the panel, records
each reviewer's verdict and provenance, and emits per-reviewer plus pairwise
dependency metrics.

This PRD defines a new public boundary, `reviewer_panel_eval_runner`. During
implementation this boundary must be registered in `docs/testing/public-boundaries.md`
before the first RED test lands. In this planning-only run, the boundary is
named here and carried into issues and TDD plans without editing repo-wide
testing contracts.

The report makes reviewer independence legible by showing raw contingency
counts, agreement rate, failure-overlap rates, Jaccard overlap for false
accepts and false blocks, and correlation values with explicit `not_applicable`
reasons when variance is zero. It is an input to future calibrated weighting,
not a weighting mechanism.

## User Stories

1. As a supervisor operator, I want to replay a labeled task set through the
   reviewer panel, so that panel behavior is measured before changing gate
   semantics.
2. As a gate owner, I want every reviewer verdict recorded for each labeled
   task, so that accepts, revises, denies, missing verdicts, and infrastructure
   failures are visible.
3. As a calibration author, I want false-accept and false-block rates per
   reviewer, so that future weighting is grounded in labeled outcomes.
4. As a calibration author, I want pairwise agreement and failure-overlap
   metrics, so that reviewer dependence is visible before any assumption of
   independence.
5. As an auditor, I want raw pairwise contingency counts next to correlation
   values, so that zero-variance and small-sample cases are not overstated.
6. As a reliability owner, I want cost and latency per reviewer, so that panel
   quality can be balanced against runtime cost in later policy work.
7. As a maintainer, I want deterministic cassettes and replay fixtures, so that
   CI can run the eval without live Cursor, Codex, Claude, Telegram, or model
   APIs by default.
8. As a supervisor operator, I want ledger and replay artifacts exported, so
   that a report can be traced back to the exact labeled set, reviewer inputs,
   cassette ids, and output hashes.
9. As a policy owner, I want the runner to be reports-only, so that eval data
   cannot silently flip aggregation, reviewer weights, or gate outcomes.
10. As a maintainer, I want this runner distinct from the lead-mode/fan-out
    eval, so that `build_agentic_eval_report(rows)` remains a lead-mode report
    aggregator and not a hidden reviewer executor.
11. As a reviewer-panel implementer, I want fixture labels to distinguish
    `accept_allowed` from `block_required`, so that false accepts and false
    blocks have stable definitions.
12. As an artifact reviewer, I want exported reports to call out missing
    reviewers separately from explicit blocking verdicts, so that
    infrastructure absence is not confused with a quality objection.

## PRD Promise Contracts

P1. Labeled Panel Replay Runner

Type: integration

User-visible promise: The supervisor can replay a labeled set of gate decisions
through the configured reviewer panel and record one result per reviewer per
labeled task.

Representative prompts or actions: Run the reviewer panel eval runner against a
fixture set with three labeled tasks: one `accept_allowed`, one
`block_required`, and one reviewer-infrastructure failure cassette.

Public boundary: `reviewer_panel_eval_runner` (new boundary to register before
implementation RED tests).

Allowed outcomes: the runner reads the labeled set, invokes or replays every
configured reviewer through deterministic fixtures/cassettes, records reviewer
id, gate, task id, decision, severity, confidence, provenance, transcript refs,
output hashes, cost, latency, and verdict-present status for every reviewer
slot.

Forbidden outcomes: only the first reviewer is evaluated; live model calls run
by default in CI; reviewer results are inferred from the conservative aggregate
instead of each reviewer verdict; or `supervisor.agentic_eval.build_agentic_eval_report`
is treated as the runner.

Related user stories: 1, 2, 7, 10, 11

P2. Per-Reviewer Quality And Runtime Metrics

Type: atomic

User-visible promise: The eval report emits per-reviewer metrics for agreement
with labels, false accepts, false blocks, unavailable or missing verdicts, cost,
and latency.

Representative prompts or actions: Inspect the report for reviewer
`independent-reviewer-0` and reviewer `independent-reviewer-1` after replaying
the labeled fixture set.

Public boundary: `reviewer_panel_eval_runner`.

Allowed outcomes: each reviewer summary includes task count, verdict-present
count, accept/revise/deny/missing counts, false-accept rate, false-block rate,
false-block cause breakdown, unavailable rate, total and average cost, total
and average latency, and optional p95 latency when sample size supports it.

Forbidden outcomes: missing verdicts are counted as accepts; false blocks hide
whether the cause was explicit revise/deny versus missing/unavailable; cost or
latency is dropped from the report; or metrics are reported without raw counts.

Related user stories: 2, 3, 6, 12

P3. Pairwise Dependency Metrics

Type: atomic

User-visible promise: The eval report makes reviewer independence and
correlation legible with pairwise metrics.

Representative prompts or actions: Inspect the pairwise section for
`independent-reviewer-0` versus `independent-reviewer-1`.

Public boundary: `reviewer_panel_eval_runner`.

Allowed outcomes: each reviewer pair includes comparable-task count, agreement
rate, disagreement counts, false-accept overlap, false-block overlap, combined
failure-overlap Jaccard, block-decision phi correlation, wrong-decision phi
correlation, cost/latency deltas or correlations where meaningful, and
`not_applicable` reasons when a denominator or variance is zero.

Forbidden outcomes: pairwise dependence is summarized only with prose; zero
variance is reported as a numeric correlation; failure overlap is computed from
aggregate panel decisions instead of per-reviewer task ids; or majority/voting
semantics are introduced.

Related user stories: 4, 5, 6

P4. Deterministic Replay And Exported Evidence

Type: integration

User-visible promise: Eval runs are deterministic and replayable, with ledger
and replay artifacts exported for audit.

Representative prompts or actions: Run the fixture eval twice and compare the
machine-readable report, replay manifest, cassette refs, and ledger event ids.

Public boundary: `reviewer_panel_eval_runner`.

Allowed outcomes: fixture runs do not touch external model services by default; cassettes
pin reviewer inputs and outputs by hash; exported artifacts include labeled-set
schema version, cassette ids, reviewer roster, report json, readable markdown,
ledger event refs, replay manifest, and raw per-reviewer rows.

Forbidden outcomes: replay depends on ambient credentials; artifacts cannot be
traced to reviewer input/output hashes; ledger refs are omitted; or rerunning
the same fixture changes metrics without an input hash change.

Related user stories: 7, 8

P5. Reports-Only Policy Boundary

Type: integration

User-visible promise: Running the eval cannot change live gate aggregation,
reviewer roster defaults, low-confidence thresholds, calibrated weights, or
policy.

Representative prompts or actions: Run the eval and inspect config, gate
payloads, and report metadata.

Public boundary: `reviewer_panel_eval_runner`.

Allowed outcomes: the report contains `policy_change_allowed=false`, lists the
non-goals, and writes only eval artifacts and ledger events marked as eval
observations.

Forbidden outcomes: weights are emitted as active policy; gate aggregation is
modified; conservative panel behavior changes; missing reviewers begin counting
as accept; or the eval runner changes config/defaults.

Related user stories: 1, 9

P6. Lead-Mode Eval Remains Distinct

Type: atomic

User-visible promise: The reviewer-panel eval runner remains distinct from the
lead-mode/fan-out eval report aggregator.

Representative prompts or actions: Run existing `agentic_eval_report` tests and
the new reviewer-panel eval runner fixture tests.

Public boundary: `agentic_eval_report` and `reviewer_panel_eval_runner`.

Allowed outcomes: `build_agentic_eval_report(rows)` continues to aggregate
historical lead-mode rows only, while the reviewer-panel runner has its own
schema version and runner/report entrypoint.

Forbidden outcomes: lead-mode rows require reviewer panel fields; reviewer
panel reports use `agentic-lead-eval/v1`; or the lead-mode report starts
executing reviewers.

Related user stories: 9, 10

## Implementation Decisions

- Add a new reviewer-panel eval runner boundary instead of extending the
  existing lead-mode report aggregator.
- Use a labeled task-set schema with at least `task_id`, `gate`, label
  `accept_allowed | block_required`, planning/evidence fixture refs, and
  expected reviewer input hash fields.
- Define false accept as a reviewer `accept` on a `block_required` task.
- Define false block as a reviewer non-accept or missing verdict on an
  `accept_allowed` task, with subcounts separating explicit revise/deny from
  missing/unavailable.
- Keep missing verdicts visible as missing/unavailable even when they contribute
  to a false-block rate.
- Emit both raw rows and aggregated summaries. Future calibration must consume
  raw counts and task ids, not only percentages.
- Compute pairwise dependency from per-reviewer task outcomes, not from the
  conservative aggregate panel decision.
- Report phi correlation only when both binary vectors have variance; otherwise
  include `correlation_status=not_applicable` with a concrete reason.
- Default automated runs to cassette/fixture replay. Any live capture or
  cassette recording mode must be explicit and out of CI defaults.
- Export eval ledger events and replay artifacts, but do not change live gate
  aggregation or policy.

## Testing Decisions

- First RED tests must exercise `reviewer_panel_eval_runner`, not helper metric
  functions.
- External reviewer execution can be faked or replayed below the runner
  boundary, but the runner must still iterate the configured panel, materialize
  per-reviewer rows, compute summaries, and export artifacts.
- Metric helper tests may follow after the runner-boundary RED test exists.
- Tests must include labeled fixtures for false accept, false block, full
  agreement, disagreement, both-reviewer failure overlap, missing verdict, zero
  variance correlation, cost, and latency.
- Tests must assert no external model service calls occur by default.
- Tests must keep `tests/test_agentic_eval.py` green to prove the lead-mode
  aggregator remains distinct.
- Full validation requires focused eval-runner tests, existing reviewer-panel
  workflow tests, existing agentic eval tests, and the full suite.

## Out of Scope

- Lead-mode/fan-out eval runner changes.
- Changing gate aggregation or conservative panel semantics.
- Introducing calibrated weighting or active policy weights.
- Changing reviewer roster defaults.
- Removing legacy `cursor_review` or `tri_agent_cursor_review` compatibility.
- Making live Cursor, Codex, Claude, Telegram, or model API calls in default
  tests.
- Retrofitting historical runs into labeled data in this slice.

## Further Notes

This is the data substrate required before calibrated weighting. It can produce
candidate facts such as "reviewer A and reviewer B false-block together on 40%
of shared failures," but it must not prescribe how to weight reviewers.
