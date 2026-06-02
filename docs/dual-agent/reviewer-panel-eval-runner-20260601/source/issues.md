# Issues: Reviewer Panel Eval Runner

Task id: `reviewer-panel-eval-runner-20260601`

## Slice 1: Register Eval Runner Boundary And Labeled Fixture Schema

Type: AFK
Priority: P1
Estimate: Medium

Blocked by: None - can start immediately

Scope:

Define the public boundary and fixture contract for reviewer-panel eval runs.
This slice introduces the `reviewer_panel_eval_runner` boundary, a labeled task
set schema, cassette refs, and deterministic fixture examples. It does not run
live reviewers yet.

PRD promise:

**Promise IDs:** P1, P4, P5, P6

**User-visible promise:** The supervisor has a named public boundary and
deterministic fixture contract for replaying labeled gate decisions through the
reviewer panel without changing policy or conflating lead-mode eval.

**Public boundary:** `reviewer_panel_eval_runner`

**Allowed outcomes:** `docs/testing/public-boundaries.md` gains a boundary
entry during implementation; fixture schema includes task id, gate, label
`accept_allowed | block_required`, reviewer input refs, cassette ids, and
hashes; fixture replay is the default mode; `agentic_eval_report` remains
unchanged.

**Forbidden outcomes:** tests target only helper functions before the public
boundary exists; fixture labels are ambiguous; external model services run by default;
or the existing lead-mode report schema is reused for reviewer-panel eval.

**Representative prompt/action:** Run the eval runner against a three-row
labeled fixture pack with cassettes for accept, block-required, and reviewer
infrastructure unavailable cases.

Acceptance criteria:

- [ ] `reviewer_panel_eval_runner` is registered as a repo public boundary
      before implementation RED tests land.
- [ ] Labeled fixture schema version is explicit and rejects unknown label
      values.
- [ ] Fixture examples cover `accept_allowed`, `block_required`, and missing or
      unavailable reviewer verdicts.
- [ ] Default eval execution mode is cassette/fixture replay, not live capture.
- [ ] Existing `agentic_eval_report` tests still pass without reviewer-panel
      fields.

TDD plan:

First public behavior: invoking `reviewer_panel_eval_runner` with a labeled
fixture pack validates labels and reports the reviewer roster without touching
external model services.

RED: add a public-boundary test that calls the runner with one valid fixture
pack and one invalid label. It should fail because the runner boundary and
schema do not exist.

GREEN: add the smallest runner entrypoint and schema validation needed to pass
the boundary test. Helper schema tests may follow after this first boundary
test exists.

## Slice 2: Replay Labeled Tasks Through Every Panel Reviewer

Type: AFK
Priority: P1
Estimate: Medium

Blocked by: Slice 1

Scope:

Implement deterministic panel replay. For every labeled task, the runner must
execute or replay each configured reviewer slot, normalize the result into a
per-reviewer row, and preserve provenance, cost, latency, transcript refs, and
hashes.

PRD promise:

**Promise IDs:** P1, P4

**User-visible promise:** A labeled eval run records one result per reviewer per
task with enough provenance to replay the run.

**Public boundary:** `reviewer_panel_eval_runner`

**Allowed outcomes:** every configured reviewer produces a row or explicit
missing/unavailable row; rows include reviewer id, task id, gate, label,
decision, severity, confidence, verdict-present flag, runtime/model/provider
metadata, transcript refs, output hash, cost, and latency; cassettes pin inputs
and outputs.

**Forbidden outcomes:** only reviewer 0 is replayed; rows are derived from the
aggregate panel decision; missing reviewers disappear; or default tests call
live reviewers.

**Representative prompt/action:** Run the runner with two reviewers and three
labeled tasks, then inspect `rows[]` in the machine-readable report.

Acceptance criteria:

- [ ] Runner iterates the full configured reviewer panel for every labeled
      task.
- [ ] Per-reviewer rows include the label and all provenance fields needed for
      replay.
- [ ] Missing or unavailable reviewers are recorded as non-accepting rows with
      cause metadata.
- [ ] Cassette input/output hashes are included in the row and report metadata.
- [ ] Re-running the same fixture produces byte-stable rows after sorting by
      task id and reviewer id.

TDD plan:

First public behavior: a fixture eval with two reviewers and three tasks emits
six per-reviewer rows without live calls.

RED: call `reviewer_panel_eval_runner` with fake cassette reviewers and assert
six rows, including one missing/unavailable row. It should fail before panel
iteration and row materialization exist.

GREEN: implement panel iteration and row normalization behind the runner
boundary. Add helper tests for row sorting and hash stability only after the
boundary test is in place.

## Slice 3: Compute Per-Reviewer Metrics

Type: AFK
Priority: P1
Estimate: Medium

Blocked by: Slice 2

Scope:

Add per-reviewer metrics over the raw eval rows. Metrics must include label
agreement, false accepts, false blocks, missing/unavailable verdicts, cost, and
latency with raw counts.

PRD promise:

**Promise IDs:** P2

**User-visible promise:** The report explains how each reviewer behaved against
the labeled set, including quality and runtime cost.

**Public boundary:** `reviewer_panel_eval_runner`

**Allowed outcomes:** each reviewer summary includes task count,
verdict-present count, accept/revise/deny/missing counts, false-accept count and
rate, false-block count and rate, false-block cause breakdown, unavailable
rate, total/average cost, total/average latency, and p95 latency when sample
size permits.

**Forbidden outcomes:** rates without counts; missing verdicts counted as
accept; false blocks without cause breakdown; cost or latency silently omitted;
or helper-only tests proving metrics without the runner boundary.

**Representative prompt/action:** Run the fixture eval and inspect
`per_reviewer["independent-reviewer-0"]` and
`per_reviewer["independent-reviewer-1"]`.

Acceptance criteria:

- [ ] False accept equals reviewer `accept` on `block_required`.
- [ ] False block equals explicit non-accept or missing verdict on
      `accept_allowed`, with cause subcounts.
- [ ] Missing/unavailable verdicts remain visible in both count and rate fields.
- [ ] Cost and latency aggregate totals and averages per reviewer.
- [ ] Metrics include raw denominators for every rate.

TDD plan:

First public behavior: running the fixture eval emits correct per-reviewer
metrics in the report.

RED: assert per-reviewer false-accept, false-block, missing, cost, and latency
values through `reviewer_panel_eval_runner`. It should fail before the metrics
summary exists.

GREEN: implement the minimal metric reducer and wire it into the runner report.
Add edge-case helper tests for empty reviewers and missing cost after the
boundary test exists.

## Slice 4: Compute Pairwise Dependency Metrics

Type: AFK
Priority: P1
Estimate: Medium

Blocked by: Slice 3

Scope:

Add pairwise reviewer metrics that make reviewer agreement, disagreement,
failure overlap, and correlation legible for future calibration.

PRD promise:

**Promise IDs:** P3

**User-visible promise:** The report explains whether reviewer pairs behave
independently or fail together on the labeled set.

**Public boundary:** `reviewer_panel_eval_runner`

**Allowed outcomes:** each pair summary includes comparable-task count,
agreement rate, disagreement counts, raw 2x2 contingency tables for block and
wrong decisions, false-accept overlap, false-block overlap, combined failure
Jaccard, block-decision phi correlation, wrong-decision phi correlation, and
`not_applicable` reasons when denominators or variance are zero.

**Forbidden outcomes:** pairwise metrics computed from aggregate panel
decisions; numeric correlation on zero variance; prose-only independence
claims; majority voting or weighting introduced in the metric layer.

**Representative prompt/action:** Inspect the pair summary for
`independent-reviewer-0` and `independent-reviewer-1` after replaying the
fixture set.

Acceptance criteria:

- [ ] Agreement and disagreement are computed from reviewer task-level
      decisions.
- [ ] False-accept and false-block overlap include raw task id sets or counts.
- [ ] Phi correlation includes raw contingency counts.
- [ ] Zero-variance cases produce `not_applicable` with reason, not `0.0`.
- [ ] Pairwise metrics do not affect gate decisions or policy fields.

TDD plan:

First public behavior: running the fixture eval emits a pairwise dependency
section with correct agreement, overlap, and correlation status.

RED: assert pairwise metrics through `reviewer_panel_eval_runner`, including a
zero-variance fixture that must produce `not_applicable`. It should fail before
pairwise metrics exist.

GREEN: add the smallest pairwise reducer and report wiring. Add helper math
tests for phi correlation after the public boundary test exists.

## Slice 5: Export Report, Ledger Events, Replay Manifest, And No-Policy Proof

Type: AFK
Priority: P1
Estimate: Medium

Blocked by: Slice 4

Scope:

Export machine-readable and readable reports plus ledger/replay artifacts. Add
tests proving the eval is reports-only and the lead-mode eval aggregator remains
separate.

PRD promise:

**Promise IDs:** P4, P5, P6

**User-visible promise:** Eval output is auditable, replayable, and unable to
change live policy or lead-mode eval behavior.

**Public boundary:** `reviewer_panel_eval_runner` and `agentic_eval_report`

**Allowed outcomes:** export includes report JSON, markdown summary, raw rows,
replay manifest, cassette refs, labeled-set hash, reviewer roster, ledger event
ids, `policy_change_allowed=false`, and non-goal metadata; existing
`build_agentic_eval_report(rows)` output remains `agentic-lead-eval/v1`.

**Forbidden outcomes:** active weights are emitted; config/defaults are
modified; gate aggregation changes; report schema is confused with
`agentic-lead-eval/v1`; or ledger/replay refs are omitted.

**Representative prompt/action:** Run the fixture eval twice, compare exported
artifacts, and run existing `tests/test_agentic_eval.py`.

Acceptance criteria:

- [ ] Report JSON and markdown include per-reviewer and pairwise metrics.
- [ ] Replay manifest records labeled-set hash, cassette ids, reviewer roster,
      and report hash.
- [ ] Ledger events identify eval run start, per-reviewer result batches, and
      final report export.
- [ ] Report includes `policy_change_allowed=false` and lists forbidden policy
      outcomes.
- [ ] Existing lead-mode eval tests stay green and schema remains
      `agentic-lead-eval/v1`.
- [ ] Full repository suite passes.

TDD plan:

First public behavior: invoking the runner with an output directory writes
report and replay artifacts and emits ledger refs without changing policy.

RED: assert exported artifact paths, report hashes, ledger event kinds, and
`policy_change_allowed=false` through the runner boundary. It should fail before
export exists.

GREEN: add minimal export and ledger event writing. Run existing
`tests/test_agentic_eval.py` to guard the separate lead-mode aggregator.

## Coverage Index

| Promise | Covered by | Status |
| --- | --- | --- |
| P1 labeled panel replay runner | Slice 1, Slice 2 | Covered |
| P2 per-reviewer quality/runtime metrics | Slice 3 | Covered |
| P3 pairwise dependency metrics | Slice 4 | Covered |
| P4 deterministic replay and exported evidence | Slice 1, Slice 2, Slice 5 | Covered |
| P5 reports-only policy boundary | Slice 1, Slice 5 | Covered |
| P6 lead-mode eval remains distinct | Slice 1, Slice 5 | Covered |
