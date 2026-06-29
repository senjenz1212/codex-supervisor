# AEB-0 Real Attempt Post-Hoc Verification

Verification date: 2026-06-25 America/Los_Angeles.

Canonical artifact:
`docs/dual-agent/auto-evolve-benchmark-promotion-bridge-20260625/artifacts/aeb0-real/official_all_arms_diagnostic_report.json`

Evidence artifact:
`docs/dual-agent/auto-evolve-benchmark-promotion-bridge-20260625/artifacts/aeb0-real/aeb0_real_attempt_evidence.json`

## Summary Verdict

The artifact is a real AEB-0 attempt, not a pre-run CLI guard artifact. It fetched the real `ScaleAI/SWE-bench_Pro` dataset, selected three real Pro instances, materialized replay/oracle inputs, and invoked `python -m swebench.harness.run_evaluation` three times through the official oracle adapter.

The artifact remains blocked and report-only. It did not reach Docker container execution. Docker daemon availability was checked (`29.4.0`), but the installed `swebench==4.1.0` harness has zero test-spec overlap with the 11 repos present in `ScaleAI/SWE-bench_Pro`, so each harness call failed during test-spec construction with `KeyError` before image build/container execution.

## Agent A: Real Attempt

Verdict: pass with caveat.

Evidence:

- `dataset-fetch.log` loads `ScaleAI/SWE-bench_Pro`, split `test`, with 731 rows.
- `aeb0_real_attempt_evidence.json` records three `swebench.harness.run_evaluation` commands.
- The canonical report has `attempt_stage: "harness"` and no `missing_cli_prerequisite:*` blocker.
- The exact real blocker is `official_harness_failed:pro_repo_specs_unavailable_in_swebench_4.1.0`.
- The Docker daemon was checked, but Docker container execution was not reached.

## Agent B: Isolation And Report-Only

Verdict: pass after freeze-summary correction.

Evidence:

- The canonical report is `status: "unavailable"` and `aeb0_artifact_gate.status: "blocked"`.
- `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `policy_mutated`, and `gate_advanced` are all false.
- `hidden_field_leak_check.ok` is true with empty refs in the public leak scan.
- Freeze-before-oracle is represented by three frozen decision rows and three oracle output paths. The canonical summary was corrected to use per-instance `frozen_decisions` when aggregate bridge rows are suppressed by oracle unavailability.
- No AutoResearch `records[]`, policy proposals, default changes, or promotion mutations are produced by this artifact.

Boundary note: `official_replay_manifest.json` contains hidden oracle fields by design and must remain an internal/oracle-side artifact, not a public reviewer packet.

## Agent C: Arm And FAR Honesty

Verdict: pass.

Evidence:

- The three selected candidates are real Pro rows but gold-patch Tier 1 smoke candidates only.
- Tier 2 is not satisfied: no oracle-bad candidate was sourced, `n_bad` is 0, and `far_unavailable:no_oracle_bad_candidates` is recorded.
- `all_arms_populated` is false, `arms_present` is empty, missing arms list baseline/S_probe/S_full/oracle_ceiling, and `far_tar_frr` is null.
- The report makes no maintainer-mergeability claim and explicitly treats SWE-bench as a held-out test-pass proxy.

## Final AEB-0 State

AEB-0 is completed as a real-attempt blocked artifact:

- Real Pro dataset fetch: yes.
- Official harness invocation: yes.
- Pre-run CLI guard blocker: no.
- Docker daemon check: yes.
- Docker container execution: no.
- Reason: Pro test specs unavailable in installed `swebench==4.1.0`.
- Authority: report-only, all promotion/policy flags false.
