# TDD Plan

Task id: `pro-single-agent-solver-20260626`

## One RED Then One Minimal GREEN

### RED 1: Generator emits k attempts with diffs through the public CLI seam

Public boundary

`tests/test_pro_single_agent_solver.py::test_generator_emits_k_attempts_with_diffs` invokes `_command_generator(...)` with a solver command. `_command_generator(...)` writes `SWEBENCH_MERGEABILITY_GENERATOR_INPUT`, expects `SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT`, and returns the solver output.

Expected failure before GREEN

The current solver raises the live-execution refusal and does not write an attempts list.

Minimal GREEN

Add a generator path that validates public input, prepares clean per-attempt git worktrees, invokes a fake runner command below the solver boundary in tests, marks untracked files intent-to-add, captures diffs with `capture_model_patch(...)`, and writes `attempts` plus first-attempt top-level compatibility fields.

### RED 2: Baseline receipt passes resolver

Public boundary

`tests/test_pro_single_agent_solver.py::test_baseline_receipt_passes_resolver`.

Expected failure before GREEN

No generator-built trusted receipt exists.

Minimal GREEN

Build `single_agent_baseline_decision` with `decision_source="single_agent_candidate_generation"`, candidate id, bool accept, matching patch hash, prompt hash, and producer model/provider/runner label.

### RED 3: Baseline receipt rejects hash mismatch

Public boundary

`tests/test_pro_single_agent_solver.py::test_baseline_receipt_rejected_on_hash_mismatch`.

Expected failure before GREEN

The generator path has no resolver-facing fail-closed proof.

Minimal GREEN

Use the existing `_resolve_powered_baseline_decision(...)` resolver on generator-emitted receipts to prove hash mismatch is unavailable. Also prove missing `candidate_id`, `model`, `provider`, and `runner_label` are unavailable.

### RED 4: Generator reads only public packet

Public boundary

`tests/test_pro_single_agent_solver.py::test_generator_reads_only_public_packet`.

Expected failure before GREEN

The solver has no public packet guard.

Minimal GREEN

Reject hidden oracle fields before any runner is invoked and leave no successful output artifact.

### RED 5: Generator attempts flatten into the candidate corpus without filtering rejects

Public boundary

`tests/test_pro_single_agent_solver.py::test_generator_attempts_feed_candidate_corpus_without_filtering_rejected_diffs`.

Expected failure before GREEN

No handoff helper exists, and `build_swe_bench_pro_candidate_corpus(...)` does not preserve produced baseline receipts from attempts.

Minimal GREEN

Add a flattening helper for solver output attempts and preserve produced baseline receipts in corpus rows.

## Focused Commands

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_pro_single_agent_solver.py -p no:cacheprovider
```

## Live Execution Artifact

The real model run is not a pytest test. It should run the solver with `--allow-live`, `--max-budget-usd > 0`, `--k > 1`, a real public input packet, and a real runner command, then commit the output or an honest unavailable manifest under this packet's `artifacts/` directory.

## Forbidden Test Shapes

- Do not mock `swe_bench_solver.main(...)` itself.
- Do not call live model APIs from pytest.
- Do not pass hidden oracle fields into a fake runner and call that isolation.
- Do not assert only helper shape without crossing the env-var generator boundary.
