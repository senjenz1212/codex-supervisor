# TDD Plan

## test_autoresearch_generator_config_loads_budget_guards_from_supervisor_config

Maps to: Slice 1, P4

Red: load a config file with an `autoresearch` section before the config model
has those fields and assert the loader cannot expose recurrence, queue, budget,
timeout, and trial settings.

Green: add `AutoResearchCfg` to supervisor config and a generator config adapter
that reads conservative defaults and explicit overrides.

## test_autoresearch_signal_generator_drafts_one_experiment_for_repeated_taxonomy_failures

Maps to: Slice 1, Slice 2, P1

Red: seed two and then three matching `dual_agent_gate_result` failure taxonomy
events into `State`, call the generator, and assert no queue API or draft row
exists.

Green: add queue persistence plus signal clustering so the third matching event
creates exactly one draft with provenance event ids, replay evaluator defaults,
configured trials, and scoped mutable paths; duplicate generation returns no
additional row.

## test_autoresearch_signal_generator_reads_reviewer_probe_and_lesson_signals

Maps to: Slice 2, P1

Red: seed reviewer disagreement events, flipping probe cohort events, and
supervisor lesson records, then call the generator before those signal sources
are understood.

Green: teach the generator to normalize those ledger sources into signal
clusters and prove each cluster drafts a row with the right provenance family.

## test_autoresearch_draft_cannot_run_until_operator_marks_runnable

Maps to: Slice 3, Slice 4, P2

Red: create a draft row and call the runner before any activation API exists;
the test expects no report, no workflow job, and no status change.

Green: add explicit activation and runner behavior so the draft is inert, the
activated row becomes runnable, and the runnable row executes through the live
AutoResearch evaluator path with a recorded workflow job and accepted report.

## test_autoresearch_auto_runner_fails_rejected_evaluator_report

Maps to: Slice 4, P2

Red: monkeypatch the live AutoResearch runner to return a report whose record
has `validation_status=rejected` and `metric_source=pending`, then assert the
queue row is incorrectly marked completed.

Green: add a runner acceptance gate so rejected or non-evaluator-executed report
records terminate the queue row as failed and emit a failed auto-run event with
the report reference and reason.

## test_autoresearch_immutable_surface_signal_becomes_report_only

Maps to: Slice 2, Slice 3, P3

Red: seed recurring failures against an immutable supervisor path and observe
that the generator either drafts a mutable experiment or has no report-only
queue state.

Green: classify immutable paths as `report_only`, include a stability proposal
pointer, clear mutable paths, keep activation from promoting the row, and assert
the runner ignores it.

## test_autoresearch_auto_runner_respects_weekly_cap

Maps to: Slice 4, P4

Red: activate two experiments and run the auto-runner with a fixed clock and cap
of one before weekly start counting exists.

Green: count rows started since the week boundary, execute only the remaining
capacity, leave extra runnable rows untouched, and make the result deterministic
under a supplied `now`.
