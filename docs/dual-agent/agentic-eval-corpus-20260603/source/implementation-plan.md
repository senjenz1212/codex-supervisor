# Implementation Plan: Agentic Eval Corpus

## Files / Modules To Touch

- `supervisor/agentic_eval_corpus.py`
- `scripts/mine_agentic_eval_cases.py`
- `tests/test_agentic_eval_corpus.py`
- `tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml`
- `tests/fixtures/agentic_eval/corpus_evidence/probe-receipts.json`
- `tests/fixtures/agentic_eval/corpus_evidence/artifact-index.json`
- `tests/fixtures/agentic_eval/corpus_evidence/diff-hunks.patch`

## Risks

- The largest risk is schema drift between the corpus and runner. The plan keeps
  this slice to a labeled-set schema and does not modify the runner's arm-level
  dataset contract.
- The second risk is accidental curation by automation. The miner refuses the
  curated corpus path and writes only staged candidates for human review.
- The third risk is accepting weak evidence. Loader validation requires concrete
  disk-resolvable evidence and cassette refs for every task.

## Traceability

- P1 -> `test_agentic_eval_labeled_set_loads_seed_fixture`
- P1 -> `test_agentic_eval_labeled_set_rejects_bad_schema_version`
- P2 -> `test_agentic_eval_labeled_set_rejects_roster_sha_mismatch`
- P2 -> `test_agentic_eval_labeled_set_rejects_missing_evidence_ref`
- P2 -> `test_agentic_eval_labeled_set_rejects_per_arm_budget`
- P3 -> `test_mine_agentic_eval_cases_is_deterministic`
- P3 -> `test_miner_stages_candidates_without_touching_curated_corpus`
- P4 -> `test_agentic_eval_labeled_set_loads_seed_fixture`
- P4 -> `test_agentic_eval_corpus_replay_does_not_call_workflow_runner`
- P5 -> `test_agentic_eval_corpus_replay_does_not_call_workflow_runner`
