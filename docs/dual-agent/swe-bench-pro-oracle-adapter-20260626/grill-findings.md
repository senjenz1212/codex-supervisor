# Grill Findings

Task id: `swe-bench-pro-oracle-adapter-20260626`

## Gate: PRD Review

Status: resolved

### Finding G1: The original harness is the wrong trust path for Pro.

Evidence: AEB-0 recorded `official_harness_failed:pro_repo_specs_unavailable_in_swebench_4.1.0` and `docker_container_not_reached:harness_spec_construction_failed_before_image_build`. The existing official Verified adapter invokes `swebench.harness.run_evaluation` in `supervisor/swe_bench_official_oracle.py` lines 87-111.

Resolution: P1 forbids depending on the `swebench` registry for Pro and requires a DockerHub Pro adapter behind the existing `oracle_runner` seam.

### Finding G2: Pro row fields are not yet carried to the post-freeze adapter context.

Evidence: `adapter_context` currently includes uppercase `FAIL_TO_PASS`, `PASS_TO_PASS`, `test_patch`, `official_patch`, and model patch data at `supervisor/swe_bench_mergeability.py` lines 1875-1909, but not `dockerhub_tag`, `before_repo_set_cmd`, `selected_test_files_to_run`, or lowercase `fail_to_pass`/`pass_to_pass`.

Resolution: P1 and P2 require passing normalized Pro row fields only into the post-freeze adapter context.

### Finding G3: A Pro adapter can accidentally erase the candidate patch.

Evidence: The pinned Pro evaluator applies the patch at `swe_bench_pro_eval.py` line 120 and then executes only the final line of `before_repo_set_cmd` at line 121 because lines 96-97 extract the last command and selected tests. Running the full multi-line `before_repo_set_cmd` after patch application would rerun `git reset --hard` and erase the candidate patch.

Resolution: P2 requires the adapter to preserve patch-before-test ordering and to record the exact setup command used in the receipt.

### Finding G4: Reward-hacking issue #93 changes artifact interpretation.

Evidence: `scaleapi/SWE-bench_Pro-os` issue #93 reports future git history and branch/tag leaks in public Pro images. That is a benchmark-integrity concern for agents running in those images, even if this adapter runs after decision freeze.

Resolution: P2 and P3 limit this slice to post-freeze oracle execution and require the real artifact to record the issue #93 caveat. It does not claim candidate-generation integrity.

### Finding G4b: The current official repo materializer preserves future Git history.

Evidence: `_default_official_repo_materializer(...)` clones with `git clone --no-checkout` and checks out `base_commit`, but does not remove `.git`, remote refs, tags, or future objects. Validator B confirmed existing AEB-0 materialized bundles still exposed remote refs and thousands of commits reachable beyond `HEAD`.

Resolution: P3 now forbids public materialized bundles retaining Git history. Implementation must remove `.git` from default materialized bundles and add a regression test for issue #93-style future-history leakage.

### Finding G5: Vendoring all Pro scripts is unnecessary for the first adapter proof.

Evidence: The pinned repository has per-instance `run_script.sh` and `parser.py` under `run_scripts/{instance_id}/`; the first required execution artifact uses three known AEB-0 instance ids.

Resolution: Vendor the three required per-instance scripts at the pinned commit for slice 1. Other rows may return `oracle_unavailable_reason="pro_script_missing"` until more scripts are vendored or a scripts directory is configured.

## Accepted Ledger Record

```jsonl
{"stage":"prd_review","task_id":"swe-bench-pro-oracle-adapter-20260626","status":"accepted","evidence":["docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/prd.md","docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/grill-findings.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
