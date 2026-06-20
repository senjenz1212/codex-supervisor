# Implementation Plan

## Files / Modules To Touch

- `supervisor/swe_bench_mergeability.py`
- `tests/test_swe_bench_pro_mergeability_bridge.py`
- `tests/fixtures/swe_bench_mergeability_fixture/README.md`
- `docs/dual-agent/swebench-mergeability-executable-runner-20260620/source/implementation-plan.md`

## Risks

- The runner could accidentally stay shallow by passing supplied decisions into the bridge instead of producing them through public execution.
- Hidden oracle files could leak through broad fixture copying, reviewer packets, command environment, or recorded receipts if isolation is not tested at the runner boundary.
- Reviewer panel infrastructure could be unavailable and still appear as a full-gate signal unless unavailable status is represented explicitly.
- A fixture report could be overclaimed as benchmark improvement unless report-only invariants and no-policy-output checks stay visible.

## Traceability

- P1 -> test_fixture_runner_freezes_decisions_before_oracle_execution
- P2 -> test_fixture_runner_executes_public_probe_and_excludes_hidden_oracle
- P2 -> test_fixture_runner_patch_apply_failure_is_recorded_not_crashed
- P3 -> test_fixture_runner_marks_full_gate_unavailable_without_panel
- P3 -> test_fixture_runner_preserves_panel_disagreement_with_probe
- P4 -> test_fixture_runner_report_only_invariants_and_no_policy_outputs

## Steps

1. Add the public runner interface and fixture metadata shape.
2. Implement public worktree preparation with protected hidden paths excluded.
3. Apply candidate patches and execute public probe commands with captured stdout, stderr, status, and command hashes.
4. Build public-only reviewer packets and run an injected reviewer panel when present.
5. Freeze baseline, S_probe, and S_full arm decisions before oracle execution.
6. Execute deterministic local oracle commands after the freeze and attach outcomes to the existing bridge report.
7. Export `report.json`, frozen decisions, command receipts, reviewer packets, and oracle receipts.

## Verification

Run the new fixture-runner tests first, then run:

```bash
pytest tests/test_swe_bench_pro_mergeability_bridge.py tests/test_swe_bench_pro_eval.py tests/test_mergeability_bench.py
```

The slice is complete only when tests are green, reviewer gates accept or record minor-only findings, and the fixture report proves decision freeze before oracle execution.
