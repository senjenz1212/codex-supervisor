# TDD Plan: Agentic Eval Corpus

## test_agentic_eval_labeled_set_loads_seed_fixture

Maps to: Slice 1, Slice 3, P1, P2, P4.

RED: calling `load_agentic_eval_labeled_set` on the seed fixture fails before
the corpus module and fixture exist.

GREEN: implement schema normalization, roster digest verification, evidence ref
resolution, and class-balanced seed fixture loading.

## test_agentic_eval_labeled_set_rejects_bad_schema_version

Maps to: Slice 1, P1.

RED: a temporary YAML with the wrong schema version is accepted.

GREEN: reject anything except `agentic-lead-eval-labeled-set/v1`.

## test_agentic_eval_labeled_set_rejects_roster_sha_mismatch

Maps to: Slice 1, P2.

RED: a corpus with a stale `roster_sha256` loads successfully.

GREEN: compute a digest from the ordered task roster and reject mismatches.

## test_agentic_eval_labeled_set_rejects_missing_evidence_ref

Maps to: Slice 1, P2.

RED: a required verdict can point at a missing file.

GREEN: resolve every `evidence_ref` against the repo root and reject missing
paths.

## test_agentic_eval_labeled_set_rejects_per_arm_budget

Maps to: Slice 1, P2, P5.

RED: a corpus can contain runner-shaped arm budgets.

GREEN: require each task budget to contain only `total_tokens` and `total_usd`.

## test_mine_agentic_eval_cases_is_deterministic

Maps to: Slice 2, P3.

RED: fixed workflow-result files cannot produce a stable candidate order.

GREEN: sort inputs and output candidate tasks deterministically.

## test_miner_stages_candidates_without_touching_curated_corpus

Maps to: Slice 2, P3, P5.

RED: the miner can write directly to the curated fixture path.

GREEN: refuse the curated path and write candidates only to a review staging
file.

## test_agentic_eval_corpus_replay_does_not_call_workflow_runner

Maps to: Slice 3, P4, P5.

RED: replay loading can accept non-fixture execution mode or expose a workflow
runner callback.

GREEN: support only `fixture_replay` and keep the corpus loader as a file-only
validator.
