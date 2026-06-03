# Test Evidence: Agentic Eval-Harness Runner

Task id: `agentic-eval-harness-runner-20260603`

## Fixture Comparison

- Dataset fixture:
  `tests/fixtures/agentic_eval/three_arm_tasks.yaml`
  - file sha256: `32f033b4863ab8155c38c8020525d1de2ff5f310914f2f600e8190db171fc150`
  - tasks: `resume-drop-catchup`, `reviewer-roster-calibration`
  - modes per task: `lead_direct`, `agentic_allowed`, `agentic_required`
  - budget equality: asserted per task for `token_budget` and `budget_usd_limit`
  - replay shape: full workflow gate sequence, P-probes, and reviewer-panel decisions required before rows materialize

## Report Artifacts

- Report:
  `docs/dual-agent/agentic-eval-harness-runner-20260603/source/agentic-eval/report.json`
  - report payload sha256: `0faf094543256389f6b1a4419c5f47780e153ea15f754f1148eb761e7434f833`
  - file sha256: `6d1c35bb06148be64951584bc9607c6cab73f50a97309dc66dcb1d2994fa8578`
- Evidence:
  `docs/dual-agent/agentic-eval-harness-runner-20260603/source/agentic-eval/evidence.json`
  - evidence payload sha256: `65a9a3475c7182cec80538fb4fe6c808e7f134ee0194388d7d696b5b35351350`
  - file sha256: `6dbe8ec173fd604f02473b27221513f5758af7eeea9f6463fe6751b282750ce8`
  - evidence records: `6`
- Replay manifest:
  `docs/dual-agent/agentic-eval-harness-runner-20260603/source/agentic-eval/replay-manifest.json`
  - file sha256: `af83ae8d984d56e994b13c874773b0b6a6b13631297cd6d4b9b282e507da66e0`
- Rows JSONL:
  `docs/dual-agent/agentic-eval-harness-runner-20260603/source/agentic-eval/rows.jsonl`
  - file sha256: `45f3a13f958747f7f975a253a0d0827fcb29c18365e46b5e7c9033053f01ef03`

## Three-Arm Summary

| mode | tasks | avg score | wall clock | cost usd | retries | rejected gates | missed issues | interventions | avg graceful degradation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `lead_direct` | 2 | 5.0 | 400.0 | 4.1 | 1 | 0 | 0 | 1 | 1.0 |
| `agentic_allowed` | 2 | 5.0 | 320.0 | 5.1 | 0 | 0 | 0 | 0 | 1.0 |
| `agentic_required` | 2 | 5.0 | 292.0 | 5.5 | 0 | 0 | 0 | 0 | 1.0 |

## Report-Only Invariants

- `default_change_allowed`: `false`
- `default_change_gate.required_modes`: `lead_direct`, `agentic_allowed`, `agentic_required`
- `agentic_lead_policy_snapshot.policy`: `off`
- `agentic_lead_policy_snapshot.mutated`: `false`
- `report_only.config_mutated`: `false`
- `report_only.policy_mutated`: `false`

## Validation Commands

- `uv run pytest tests/test_agentic_eval.py -q` -> `8 passed in 0.20s`
- `uv run python -m py_compile supervisor/agentic_eval.py tests/test_agentic_eval.py` -> passed
- `git diff --check` -> passed
- `uv run pytest tests/test_agentic_eval.py tests/test_agentic_executor.py tests/test_dual_agent_workflow_driver.py -q` -> `117 passed in 119.92s`
- `uv run --extra dev pytest -q` -> `651 passed in 146.27s`

## Acceptance Mapping

- Three-arm row coverage: `test_agentic_eval_runner_covers_required_modes`
- Equal per-task compute budget: `test_agentic_eval_runner_enforces_equal_budget`
- Real workflow-shaped replay cassette: `test_agentic_eval_runner_requires_gated_replay_shape`
- Deterministic decision-tree score: `test_agentic_eval_decision_tree_is_deterministic`
- Evidence-required and resolvable-reference verdict scoring: `test_agentic_eval_requires_evidence_for_verdict`
- Report-only invariant and artifact export: `test_agentic_eval_runner_is_report_only`
- Replay no-live-call guard: `test_agentic_eval_replay_blocks_live_calls`
