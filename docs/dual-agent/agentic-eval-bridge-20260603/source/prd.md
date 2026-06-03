# PRD: Agentic Eval Bridge

Task id: `agentic-eval-bridge-20260603`

## Problem Statement

The agentic eval runner can compare `lead_direct`, `agentic_allowed`, and
`agentic_required`, but the current dataset fixture has hand-authored arms.
That proves report plumbing, not whether fan-out wins at equal budget. The
new curated corpus has real labeled cases, but it has no bridge that runs each
case through all three workflow modes, records the outcomes, and produces a
replayable three-arm dataset.

## Solution

Add a report-only bridge that turns a validated
`agentic-lead-eval-labeled-set/v1` case into an
`agentic-lead-eval-dataset/v1` task by invoking a supplied workflow runner once
per required mode. The live runner is opt-in, passes `agentic_lead_policy` as a
per-arm request parameter, records every arm result to cassettes, assembles a
dataset, and then runs the existing `agentic_eval_runner` in fixture replay.
Replay and tests never call live agents.

## User Stories

- As an operator, I can generate a real three-arm dataset from curated cases
  without hand-authoring arm outcomes.
- As a reviewer, I can replay the recorded dataset and get the same comparison
  report without provider calls.
- As a maintainer, I can verify equal total budget across modes and confirm
  fan-out did not receive extra budget or mutate the default policy.

## PRD Promise Contracts

P1. Labeled Case To Three-Arm Dataset

- Public boundary: `supervisor.agentic_eval_assembler.assemble_agentic_eval_task`.
- Allowed outcomes: one task with exactly `lead_direct`, `agentic_allowed`, and
  `agentic_required` arms; each arm is backed by a workflow result produced by
  the supplied workflow runner; required verdicts are preserved.
- Forbidden outcomes: hand-authored arm data; missing required modes; arms that
  cannot be loaded by `agentic_eval_runner`.

P2. Equal Total Budget And Fan-Out Split

- Public boundary:
  `supervisor.agentic_eval_assembler.compute_arm_budget_split`.
- Allowed outcomes: all arms expose the same total token and dollar budget;
  fan-out arms split that total across the lead and workers; worker budget is
  carved from the total, not added to it.
- Forbidden outcomes: per-mode total-budget drift; fan-out budget in addition
  to the lead budget; silent fallback to unequal budgets.

P3. Opt-In Live Workflow Runner

- Public boundary: `scripts/run_agentic_eval_live.py`.
- Allowed outcomes: live execution is refused unless `--allow-live-calls` is
  provided; per-arm workflow requests pass policy as arguments; cassettes and
  assembled dataset are written for replay.
- Forbidden outcomes: default live calls; config/default mutation; direct
  `agentic_lead_policy` changes outside the workflow request.

P4. Record And Replay Determinism

- Public boundary:
  `supervisor.agentic_eval_assembler.write_agentic_eval_dataset` plus
  `supervisor.agentic_eval.agentic_eval_runner`.
- Allowed outcomes: recorded cassettes replay to the same report SHA; default
  fixture replay does not call providers or subprocess workflow arms.
- Forbidden outcomes: replay requiring external model services; cassettes ignored in
  favor of synthetic arms; non-deterministic report output for fixed input.

P5. Report-Only Decision Artifact

- Public boundary: exported report under
  `docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/`.
- Allowed outcomes: comparison contains score, cost, wall time, retries,
  rejected gates, and missed issues for each mode; `default_change_allowed`
  remains false; policy snapshot remains off.
- Forbidden outcomes: enabling fan-out by default; touching `supervisor/state.py`
  or production scaling; changing reviewer-panel or gate semantics.

## Implementation Decisions

- Add a new `supervisor/agentic_eval_assembler.py` module so bridge logic stays
  separate from corpus curation and existing report aggregation.
- Reuse `load_agentic_eval_labeled_set` for input validation and
  `agentic_eval_runner` for replay scoring instead of introducing a parallel
  schema or scorer.
- Store recorded bridge artifacts under the existing
  `tests/fixtures/agentic_eval/` and `docs/dual-agent/.../agentic-eval-live/`
  conventions.
- Implement the live workflow runner as a CLI-driven adapter over
  `mcp_tools.codex_supervisor_workflow_cli`, keeping all policy choices in the
  per-arm request payload.

## Testing Decisions

- Public-boundary tests start at the assembler and live CLI refusal boundary.
- Fake workflow runners stand in for external model services in tests; replay fixtures
  are recorded dictionaries, not live executions.
- Tests assert equal total budget and inspect the fan-out split metadata so the
  bridge cannot smuggle extra worker budget into agentic modes.
- Replay determinism is proven by running the existing runner twice over the
  assembled fixture and comparing `report_sha256`.

## Out Of Scope

- Flipping `agentic_lead_policy` or enabling fan-out for any task class.
- Changing `AGENTIC_WORKER_MAX_SUBAGENTS`.
- Changing production reviewer-panel semantics.
- Changing `supervisor/state.py`, scaling, or storage schemas.
