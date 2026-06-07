# PRD: Supervisor AutoResearch Foundation

Task id: `supervisor-autoresearch-foundation-20260606`

## Problem Statement

The supervisor needs a safe way to experiment with its own prompts, rubrics,
worker rosters, and evaluation overlays without letting a successful-looking
experiment mutate production behavior. The current agentic evaluation path can
score fixed datasets, but there is no first-class AutoResearch domain layer for
recording experiment attempts, validating their mutable surface, reducing
repeated trial metrics, and emitting a report-only recommendation.

The failure mode to avoid is self-modifying supervisor code that optimizes a
visible metric while weakening gates, scorer code, evidence checks, or reviewer
authority. The new foundation must preserve the existing truth model: the
ledger records durable evidence, filesystem files remain artifacts, validators
emit evidence only, gates remain authoritative, and Cursor stays an independent
reviewer.

## Solution

Add a small `supervisor.autoresearch` package that models experiments,
attempts, validation reports, and report exports. The first slice is fixture
only and deterministic. It records additive ledger events through the existing
state interface, validates that changed files are limited to configured mutable
paths, rejects attempts that touch immutable evaluator or supervisor authority
surfaces, reduces repeated metrics to median and interquartile range, and emits
recommendations with `default_change_allowed=false`.

The package does not run live model calls by default. A script may load fixture
input and write a stable report, but non-fixture execution must require an
explicit `--allow-live` flag and must still produce report-only output. The
implementation keeps the current dual-agent gate sequence, reviewer panel, typed
outcomes, receipts, and policy defaults untouched.

## User Stories

1. As a supervisor maintainer, I can define an experiment that compares a
   candidate supervisor-improvement attempt against a known baseline while
   keeping evaluator and gate surfaces immutable.
2. As a reviewer, I can inspect one ledger-backed attempt record and see the
   changed files, artifact hashes, repeated trial metrics, cost, wall clock, and
   validation status.
3. As an operator, I can run the fixture AutoResearch path in CI without
   contacting live model providers, subprocess agents, Cursor, or external
   services.
4. As a gate owner, I can trust that validation reports cannot advance gates,
   flip `agentic_lead_policy`, or set `default_change_allowed=true`.
5. As a future evaluator owner, I can see why an attempt was rejected when it
   touched scorer code, omitted evidence, produced unstable trial metrics, or
   changed immutable supervisor authority files.

## PRD Promise Contracts

P1. ledger-backed-autoresearch-attempts

- User-visible promise: Starting and completing an AutoResearch experiment or
  attempt writes durable, typed ledger events with replayable payloads.
- Representative action: Call the public orchestrator with an in-memory state
  recorder and one fixture attempt.
- Public boundary: `supervisor.autoresearch.orchestrator`.
- Allowed outcomes: additive event kinds record experiment start, attempt start,
  attempt completion, validation start, validation completion, and report
  emission; event payloads contain IDs, paths, metrics, hashes, and statuses.
- Forbidden outcomes: silent filesystem-only state, untracked TSV truth, or
  direct mutation of existing workflow job or gate tables.
- Related user stories: 1, 2.

P2. immutable-surface-validation

- User-visible promise: Attempts can modify allowlisted mutable paths only and
  fail validation if they touch immutable evaluator, gate, ledger, reviewer, or
  validation surfaces.
- Representative action: Validate one mutable-only attempt and one attempt that
  changes `supervisor/state.py` or scorer fixtures.
- Public boundary: `supervisor.autoresearch.validation.validate_attempt`.
- Allowed outcomes: mutable-only attempts pass surface validation; immutable
  touches produce `validation_status=rejected` and a gaming flag.
- Forbidden outcomes: evaluator mutation, gate weakening, reviewer aggregation
  edits, or validation-code changes passing as successful research.
- Related user stories: 1, 5.

P3. repeated-trial-report-only-metrics

- User-visible promise: Validation reduces repeated metric trials to median and
  IQR, flags unstable values, and keeps every report recommendation
  report-only.
- Representative action: Build a report from fixture trials with stable and
  unstable metrics.
- Public boundary: `supervisor.autoresearch.report.build_autoresearch_report`.
- Allowed outcomes: reports include median, IQR, unstable-trial flags, and
  `default_change_allowed=false`.
- Forbidden outcomes: mean-only promotion, single lucky run acceptance, or any
  code path setting `default_change_allowed=true`.
- Related user stories: 2, 4, 5.

P4. fixture-only-default

- User-visible promise: The default runner consumes fixtures and refuses live
  execution unless the caller explicitly opts in.
- Representative action: Run the script without `--allow-live`, then request a
  live mode sentinel.
- Public boundary: `scripts/run_supervisor_autoresearch.py`.
- Allowed outcomes: fixture replay writes deterministic report artifacts; live
  mode raises unless `--allow-live` is supplied.
- Forbidden outcomes: hidden model calls, subprocess agent spawns, Cursor calls,
  network/provider calls, or production workflow execution in default mode.
- Related user stories: 3.

P5. gate-authority-preserved

- User-visible promise: AutoResearch validators emit evidence and
  recommendations only; they cannot advance dual-agent gates or mutate
  production defaults.
- Representative action: Inspect a validation report and the package code for
  gate or config mutation behavior.
- Public boundary: `supervisor.autoresearch.validation` and
  `supervisor.autoresearch.report`.
- Allowed outcomes: validator outputs are ledger evidence, policy snapshots show
  no mutation, and Cursor reviewer defaults remain compatible with the workflow.
- Forbidden outcomes: direct calls that mark gates accepted, policy flips,
  default config edits, or replacing reviewer-panel authority with a score.
- Related user stories: 4.

## Implementation Decisions

- Add a new package under `supervisor/autoresearch/` with dataclasses and pure
  functions so the first slice stays easy to replay in tests.
- Use existing `State.write_event` shape without changing `supervisor/state.py`.
- Treat changed-file validation as a deterministic path-prefix and exact-path
  check. Caller-supplied immutable paths are combined with conservative default
  immutable prefixes for supervisor authority surfaces.
- Store artifact hashes in payloads as caller-provided evidence and verify
  expected hashes when fixture files exist on disk.
- Keep the script fixture-only by default; live execution is an explicit guard
  path for future slices, not a current integration.
- Export sorted JSON with stable SHA-256 report hashes.

## Testing Decisions

- The first RED test targets the public orchestrator boundary and asserts that
  experiment and attempt ledger events are emitted.
- Validation tests cover immutable path rejection, mutable-only acceptance,
  missing evidence flags, and report-only status.
- Report tests cover repeated-trial median, IQR, unstable metrics, and
  `default_change_allowed=false`.
- Runner tests cover fixture replay output and the live-call guard.
- Existing configuration tests or direct config assertions cover Cursor reviewer
  compatibility without requiring a live Cursor call.

## Out Of Scope

- Do not build broad tree search, autonomous overnight loops, or live worker
  spawning in this slice.
- Do not add a new durable runtime, Postgres migration, or queue architecture.
- Do not change `agentic_lead_policy`, Cursor defaults, reviewer aggregation,
  dual-agent gate semantics, or production workflow job lifecycle.
- Do not allow AutoResearch to merge, commit, or promote a winning attempt.
- Do not treat LLM judge acceptance as authoritative without existing gates.
