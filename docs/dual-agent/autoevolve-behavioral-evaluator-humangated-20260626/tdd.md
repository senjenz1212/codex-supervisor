# TDD Plan

## First RED

`tests/test_autoevolve_behavioral.py::test_behavioral_evaluator_reads_candidate`

Run the mergeability evaluator executable with the same relative candidate ref in two different attempt worktrees. The good worktree candidate should score higher than the bad worktree candidate. This initially fails because candidate resolution does not prefer `--attempt-worktree`.

## Minimal GREEN

Thread `--attempt-worktree` into the evaluator candidate resolver and check `attempt_worktree / relative_candidate_ref` before source-root or bench-root fallbacks.

## Next RED

`tests/test_autoevolve_behavioral.py::test_replay_corpus_fixture_metric_rejected_as_adoption_signal`

Create an unresolved AutoResearch experiment and assert defaults select the behavioral evaluator. Then feed an otherwise applyable policy record carrying `evaluator_ref=supervisor/autoresearch/evaluators/replay_corpus.py` into policy derivation and assert no proposal derives.

## Minimal GREEN

Default unresolved experiments to the mergeability evaluator and add evaluator provenance to validation records. Reject replay-corpus records in the policy derivation applyability guard.

## Next RED

`tests/test_autoevolve_behavioral.py::test_benchmark_report_not_on_derivation_path`

Feed an otherwise applyable benchmark-promotion-shaped record into policy derivation and assert no proposal derives.

## Minimal GREEN

Reject benchmark-promotion records in the same record applyability guard.

## Next RED

`tests/test_autoevolve_behavioral.py::test_adoption_requires_named_operator`

Derive a draft from an accepted behavioral evaluator record, assert it still requires operator approval, and prove `approve_policy_proposal` rejects a reserved service approver.

## Minimal GREEN

No new application path should be needed; keep `_require_operator` and authority invariants intact, and reject reserved service identities.

## Boundary Guard REDs

`tests/test_codex_supervisor_axi.py::test_axi_experiments_activate_requires_named_operator`

`tests/test_codex_supervisor_axi.py::test_axi_policy_approve_requires_named_approver`

Invoke the public AXI CLI without `--operator` / `--approver` and assert the command fails without activating an experiment or applying a policy overlay.

## Minimal GREEN

Remove the prior `codex-supervisor-axi` CLI default identity and let the underlying human-gate validators reject missing names.

## Focused Verification

Run:

```bash
.venv/bin/python -m pytest tests/test_autoevolve_behavioral.py tests/test_autoresearch.py::test_autoresearch_default_behavioral_evaluator_produces_mergeability_score tests/test_autoresearch_generator.py::test_autoresearch_signal_generator_drafts_one_experiment_for_repeated_taxonomy_failures
.venv/bin/python -m pytest tests/test_codex_supervisor_axi.py::test_axi_experiments_activate_requires_named_operator tests/test_codex_supervisor_axi.py::test_axi_policy_approve_requires_named_approver -q
```

The old replay-corpus default test is expected to be renamed to the behavioral default contract.
