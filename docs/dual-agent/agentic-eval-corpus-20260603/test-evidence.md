# Test Evidence: Agentic Eval Corpus

Task id: `agentic-eval-corpus-20260603`

## Commands

- `uv run pytest tests/test_agentic_eval_corpus.py -q`
  - Result: `9 passed`
- `uv run python -m py_compile supervisor/agentic_eval_corpus.py scripts/mine_agentic_eval_cases.py tests/test_agentic_eval_corpus.py`
  - Result: passed
- `uv run pytest tests/test_agentic_eval_corpus.py tests/test_agentic_eval.py tests/test_reviewer_panel_eval_runner.py -q`
  - Result: `26 passed`
- `git diff --check`
  - Result: passed
- `uv run --extra dev pytest -q`
  - Result: `660 passed in 138.70s`

## Seed Set

Fixture: `tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml`

- Fixture sha256:
  `46b9ce0b61b1596b9e0cb336b7886934d10a1a6dd94bb933147b20d6ef514ac4`
- Roster sha256:
  `f09369bb8199a92a955603d86d80a47cf1ffd115ee00534447e4ccbd5220c728`
- Probe evidence sha256:
  `ca2bae23de25d48945cd022685e6dbaa03cacba1ef14a5973a6fcf22336e4c4f`
- Artifact index sha256:
  `b5d9a7e91b7b1f2effe4a358bb47c44afa6e574cc1edf72766b3f0ea1ad18005`

Seed tasks:

- `clean-accept-runner-report`: accept;
  `report_only_invariant`, `evidence_artifact_available`
- `planning-artifact-deny`: deny;
  `missing_artifact_blocks`, `artifact_rigor_recorded`
- `reviewer-unavailable-revise`: revise;
  `missing_verdict_not_accept`, `recovery_packet_available`
- `artifact-only-skill-receipt-accept`: accept;
  `skill_receipts_present`, `planning_hashes_stable`
- `multi-file-change-accept`: accept;
  `multi_file_diff_scoped`, `regression_tests_recorded`
- `external-call-guard-deny`: deny;
  `replay_guard_blocks_execution`, `guard_artifact_available`
- `equal-budget-clean-accept`: accept;
  `budget_shape_task_level`, `equal_budget_probe_recorded`
- `evidence-ref-revise`: revise;
  `evidence_resolution_checked`, `revise_when_evidence_missing`

## Miner Staging Sample

Command:

`uv run python scripts/mine_agentic_eval_cases.py --handoff-dir .handoff --output .scratch/agentic-eval/agentic-eval-corpus-20260603-candidates.json --repo-root .`

Result:

- status: `staged`
- curation_required: `true`
- candidate_count: `2`
- staged output:
  `.scratch/agentic-eval/agentic-eval-corpus-20260603-candidates.json`
- staged output sha256:
  `28ad9bf28219398bd2864b096836cf86fad543b5c2a39a1c2ec3a2c586705703`

Candidates:

- `reviewer-panel-foundation-20260601`: accept;
  `outcome_review_decision_recorded`, `artifact_export_recorded`;
  mined from `.handoff/reviewer-panel-foundation-20260601-workflow-result.json`
- `reviewer-unavailable-recovery-20260531`: revise;
  `tdd_review_decision_recorded`, `artifact_export_recorded`;
  mined from `.handoff/reviewer-unavailable-recovery-20260531-workflow-result.json`

## Report-Only Invariants

- The corpus loader exposes no `workflow_runner` callback.
- The loader rejects non-`fixture_replay` execution mode.
- The miner refuses to write to the curated corpus path.
- No config file, `supervisor/state.py`, or agentic policy default was edited.
