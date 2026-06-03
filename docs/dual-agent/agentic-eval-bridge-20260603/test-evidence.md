# Test Evidence: Agentic Eval Bridge

Task id: `agentic-eval-bridge-20260603`

## Commands

- `uv run pytest tests/test_agentic_eval_bridge.py tests/test_agentic_eval.py -q`
  - Result: `16 passed`
- `uv run pytest tests/test_agentic_eval_bridge.py tests/test_agentic_eval.py tests/test_agentic_eval_corpus.py tests/test_reviewer_panel_eval_runner.py -q`
  - Result: `34 passed`
- `uv run python -m py_compile supervisor/agentic_eval.py supervisor/agentic_eval_assembler.py scripts/run_agentic_eval_live.py tests/test_agentic_eval_bridge.py tests/test_agentic_eval.py`
  - Result: passed
- `git diff --check`
  - Result: passed
- `uv run --extra dev pytest -q`
  - Result: `668 passed in 126.63s`

## Live Three-Arm Recording

Command:

`uv run python scripts/run_agentic_eval_live.py --allow-live-calls --labeled-set tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml --output-dir docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live --task-id clean-accept-runner-report --task-id planning-artifact-deny --task-id reviewer-unavailable-revise --min-subagents 1 --quality best --timeout-s 900`

Result:

- Real workflow arms recorded: 9
- Dataset:
  `docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/agentic_eval_three_arm_dataset.yaml`
- Cassettes:
  `docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/cassettes/`
- Report:
  `docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live/report/report.json`
- Report sha256:
  `bc844776c28945364d16facea9659c9f4335cf6692eeef56bb949a9bae3b0187`

Rows:

| task_id | mode | score | cost_usd | wall_clock_s | retries | rejected_gates | missed_issues | workflow_status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| clean-accept-runner-report | lead_direct | 0.0 | 0.0 | 3.372 | 0 | 2 | 0 | blocked |
| clean-accept-runner-report | agentic_allowed | 0.0 | 0.0 | 151.141 | 0 | 2 | 0 | blocked |
| clean-accept-runner-report | agentic_required | 0.0 | 0.0 | 515.577 | 0 | 2 | 0 | blocked |
| planning-artifact-deny | lead_direct | 5.0 | 0.0 | 3.383 | 0 | 2 | 0 | blocked |
| planning-artifact-deny | agentic_allowed | 5.0 | 0.0 | 127.149 | 0 | 2 | 0 | blocked |
| planning-artifact-deny | agentic_required | 5.0 | 0.0 | 93.672 | 0 | 2 | 0 | blocked |
| reviewer-unavailable-revise | lead_direct | 5.0 | 0.0 | 3.325 | 0 | 2 | 0 | blocked |
| reviewer-unavailable-revise | agentic_allowed | 5.0 | 0.0 | 116.766 | 0 | 2 | 0 | blocked |
| reviewer-unavailable-revise | agentic_required | 5.0 | 0.0 | 124.920 | 0 | 2 | 0 | blocked |

Summary:

| mode | total_score | avg_score | total_wall_clock_s | avg_wall_clock_s | total_cost_usd | rejected_gates |
|---|---:|---:|---:|---:|---:|---:|
| lead_direct | 10.0 | 3.333 | 10.080 | 3.360 | 0.0 | 6 |
| agentic_allowed | 10.0 | 3.333 | 395.056 | 131.685 | 0.0 | 6 |
| agentic_required | 10.0 | 3.333 | 734.169 | 244.723 | 0.0 | 6 |

Verdict: fan-out does not win at equal budget on this 3-case sample. Scores are
tied, while both agentic modes take materially longer. `agentic_required` also
failed the clean-accept case because the real workflow blocked before a terminal
accept.

## Report-Only Invariants

- `default_change_allowed`: false.
- `agentic_lead_policy_snapshot.policy`: `off`.
- `agentic_lead_policy_snapshot.mutated`: false.
- `report_only.policy_mutated`: false.
- `report_only.config_mutated`: false.
- No `supervisor/state.py`, config default, reviewer-panel, or production
  scaling files were edited.
