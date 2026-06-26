# Tri-Agent Findings

Task id: `swe-bench-pro-oracle-adapter-20260626`

## Validator A: Contract Conformance

Agent: `019f02bd-5261-70f3-94a0-f741e41afea6`

Verdict: REVISE

Strongest objection: the Pro oracle adapter cannot return raw harness output. The existing bridge consumes only the normalized status contract accepted by `_normalise_oracle_adapter_outcome(...)` and `_interpret_oracle_outcome(...)`.

Evidence:

- `_interpret_oracle_outcome(...)` accepts only `pass`, `fail`, or `unavailable` bucket statuses at `supervisor/swe_bench_mergeability.py:369`.
- `_normalise_oracle_adapter_outcome(...)` delegates into the same interpreter at `supervisor/swe_bench_mergeability.py:1228`.
- Existing adapter kinds are constrained at `supervisor/swe_bench_mergeability.py:816` and validated at `supervisor/swe_bench_mergeability.py:3399`.
- The existing official adapter already maps harness reports into normalized status output at `supervisor/swe_bench_official_oracle.py:178`.

Required remediation accepted:

- Return `fail_to_pass_status`, `pass_to_pass_status`, optional `oracle_unavailable`, optional `oracle_unavailable_reason`, and `oracle_adapter_receipt`.
- Keep `oracle_adapter_kind="official_docker_or_equivalent"` unless every allowlist is updated.
- Cover `pass/pass`, `fail/pass`, `pass/fail`, and unavailable.
- Exercise the bridge interpretation contract, not parser helpers only.

## Validator B: Isolation And History Leakage

Agent: `019f02bd-6bb2-75a0-999b-52f81aeca821`

Verdict: REVISE

Strongest objection: the default official repo materializer leaves `.git` metadata in materialized public bundles. That preserves remote refs, tags, and future commits reachable from the public-looking bundle, matching the reward-hacking risk described in scaleapi/SWE-bench_Pro-os issue #93.

Evidence:

- `_default_official_repo_materializer(...)` clones with `git clone --no-checkout` at `supervisor/swe_bench_mergeability.py:3853`.
- It then checks out only `base_commit` at `supervisor/swe_bench_mergeability.py:3864`.
- Freeze-before-oracle is already present: decisions are written at `supervisor/swe_bench_mergeability.py:1794`, persisted before oracle execution at `supervisor/swe_bench_mergeability.py:1838`, and oracle execution starts later at `supervisor/swe_bench_mergeability.py:1851`.
- Public/reviewer packet stripping is covered by public packet construction at `supervisor/swe_bench_mergeability.py:194`, reviewer packet construction from `public_task` at `supervisor/swe_bench_mergeability.py:1113`, and leak scans at `supervisor/swe_bench_mergeability.py:1696`.

Required remediation accepted:

- Harden `_default_official_repo_materializer(...)` so returned public bundles are detached work trees with no `.git`.
- Add an issue #93-style regression test with refs/tags/future commits in the source repo.
- Expand public leak checks for hidden lowercase Pro fields and setup/test-file fields.
- Treat replay manifests carrying oracle-only fields as private oracle state, not public packets.

## Validator C: Real Attempt Honesty

Agent: `019f02bd-8581-74e1-b2ed-aaefe253b219`

Verdict: PASS, with hard caveat

Strongest objection: the existing AEB-0 artifact is a real attempt, but not Pro grading success and not Docker execution. It failed at `attempt_stage="harness"` before image build or container execution. The Pro adapter must preserve unavailable/blocker states honestly.

Evidence:

- AEB-0 records `attempt_stage="harness"` and blockers `official_harness_failed:pro_repo_specs_unavailable_in_swebench_4.1.0` plus `docker_container_not_reached:harness_spec_construction_failed_before_image_build` in `docs/dual-agent/auto-evolve-benchmark-promotion-bridge-20260625/artifacts/aeb0-real/aeb0_real_attempt_evidence.json:2`.
- The artifact shows `KeyError` for unsupported Pro repos such as `NodeBB/NodeBB` and `qutebrowser/qutebrowser` in the same evidence file.
- The canonical report keeps AEB-0 blocked and authority flags false in `docs/dual-agent/auto-evolve-benchmark-promotion-bridge-20260625/artifacts/aeb0-real/official_all_arms_diagnostic_report.json:26`.
- Installed `swebench==4.1.0` uses `MAP_REPO_VERSION_TO_SPECS[repo][version]` in `.venv/lib/python3.12/site-packages/swebench/harness/test_spec/test_spec.py:207`, which is the registry blocker for Pro rows.

Required remediation accepted:

- Do not route Pro through the existing Verified adapter.
- Use accepted coarse attempt stages such as `dataset_fetch`, `docker`, `harness`, and `scoring`, with precise substage detail in receipts and unavailable reasons.
- Return unavailable through the bridge for Pro Docker or parser failures.
- Include command, return code, stdout/stderr hashes, artifact paths, Docker/harness metadata, statuses, and unavailable reason in receipts.
- Keep hidden Pro oracle fields out of reviewer/public packets and only pass them after freeze.

## Folded Remediation Ledger

```jsonl
{"stage":"tri_agent_validation","task_id":"swe-bench-pro-oracle-adapter-20260626","status":"accepted_with_remediation","validators":{"contract_conformance":"revise","isolation_history_leakage":"revise","real_attempt_honesty":"pass_with_caveat"},"required_changes":["normalized adapter output","explicit pass/pass fail/pass pass/fail unavailable tests","materialized public bundles have no .git","lowercase Pro hidden-field leak coverage","honest unavailable states for failed real attempts"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
