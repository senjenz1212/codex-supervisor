# SWE-bench Pro Oracle Adapter PRD

Task id: `swe-bench-pro-oracle-adapter-20260626`

## Problem Statement

The current official SWE-bench replay path has the right mergeability bridge and post-freeze `oracle_runner` seam, but the installed `swebench==4.1.0` harness cannot grade ScaleAI SWE-bench Pro rows. The AEB-0 real attempt failed during test-spec construction before Docker because the harness registry has no Pro repo specs for rows such as `NodeBB/NodeBB`. That leaves Pro benchmark evidence blocked unless the existing oracle seam can run the public Pro Docker images and return the bridge's status contract honestly.

## Solution

Add a SWE-bench Pro Docker oracle adapter behind the existing `oracle_runner` interface. The adapter uses Pro row fields only after decision freeze, pulls `jefzda/sweap-images:{dockerhub_tag}`, writes the pinned per-instance `run_script.sh` and `parser.py`, applies the model patch, runs the selected tests, parses the resulting test list, and returns either pass/fail statuses or an honest unavailable result. This does not replace the Verified path, does not modify the `swebench` dependency, and does not create an autonomous benchmark-to-policy bridge.

## PRD Promise Contracts

### P1. A Pro oracle runner returns the bridge status contract.

Public boundary

The callable injected as `oracle_runner` into `swebench_mergeability_replay_runner(...)` and reached from `supervisor/swe_bench_mergeability.py` lines 1866-1919.

Chosen seam

The existing adapter context and normalization chain: `adapter_context` construction at `supervisor/swe_bench_mergeability.py` lines 1875-1909, `_normalise_oracle_adapter_outcome(...)` at lines 1225-1242, and `_interpret_oracle_outcome(...)` at lines 369-409.

Allowed outcomes

- `{fail_to_pass_status, pass_to_pass_status}` where both statuses are `pass` or `fail`, plus `oracle_adapter_receipt`.
- `{oracle_unavailable: true, oracle_unavailable_reason: <reason>}` when Docker, scripts, parser output, or required Pro row fields are unavailable.
- The Verified SWE-bench adapter continues to use `swebench.harness.run_evaluation`.

Forbidden outcomes

- Returning `pass` for missing, empty, malformed, or unparsed Pro output.
- Raising past the `oracle_runner` boundary for ordinary Docker or parser failures.
- Depending on `swebench` test-spec registry for Pro rows.
- Flipping any authority flag to true.

### P2. Status is computed from real container test results.

Public boundary

The adapter result mapping for three real SWE-bench Pro instances selected from the existing AEB-0 run inputs.

Chosen seam

`jefzda/sweap-images:{dockerhub_tag}` pull and container run, pinned `run_script.sh`, pinned `parser.py`, selected test file execution, parser output, and subset classification of `fail_to_pass` and `pass_to_pass`.

Allowed outcomes

- Gold patch on a completed Pro run returns both statuses as `pass`.
- A deliberately broken patch returns `fail_to_pass_status="fail"` when Pro parser output shows required fail-to-pass tests did not pass.
- If the image, scripts, or parser cannot complete, the adapter records `oracle_unavailable` with the real stage and receipt hashes.

Forbidden outcomes

- Building or grading Pro with the installed SWE-bench Verified harness.
- Trusting a `git reset` or checkout sequence that erases the candidate patch before tests.
- Treating a real attempt blocked at pull, container start, or parse as a benchmark success.

### P3. Oracle isolation is preserved.

Public boundary

The existing reviewer leak scan, frozen decisions artifact, and official replay receipt validation.

Chosen seam

`swebench_mergeability_official_replay_runner(...)` only passes Pro oracle fields into candidate/oracle context after public packets and frozen decisions are written.

Allowed outcomes

- `fail_to_pass`, `pass_to_pass`, `FAIL_TO_PASS`, `PASS_TO_PASS`, `test_patch`, selected test files, and Pro setup commands stay out of public/reviewer packets.
- The adapter runs only after `frozen_decisions.json` exists.
- Leak checks and receipt validation stay green or suppress metrics.

Forbidden outcomes

- Hidden Pro oracle fields reaching public packets, reviewer packets, generator inputs, or frozen decision rows.
- Public materialized bundles retaining `.git`, remote refs, tags, or future-history commits reachable by an agent.
- Recording a bare label-only receipt for `official_docker_or_equivalent`.
- Any claim that report-only benchmark evidence mutates policy or advances a gate.

## User Stories

1. As the operator, I want Pro rows to be graded through the existing oracle seam, so that the bridge can move past the `swebench==4.1.0` registry blocker.
2. As the operator, I want Docker and parser failures to be explicit unavailable states, so that a blocked real attempt is not misreported as success.
3. As the operator, I want Pro hidden fields isolated until after freeze, so that reviewer and generator packets cannot exploit oracle labels.
4. As the operator, I want the Verified path left alone, so that Pro support does not regress the already-working official replay smoke path.
5. As the operator, I want a real three-instance artifact, so that future benchmark work can distinguish adapter plumbing from proven Pro execution.

## Implementation Decisions

- Fill the existing `oracle_runner` interface; do not add a parallel mergeability architecture.
- Put the Pro implementation in the existing official oracle adapter module rather than creating a new standalone evaluator.
- Vendor only the per-instance Pro scripts needed for the first real artifact, pinned to `scaleapi/SWE-bench_Pro-os` commit `ca10a60a5fcae51e6948ffe1485d4153d421e6c5`; missing scripts for other rows are an honest unavailable state until vendored or configured.
- Use Docker CLI invocations below the adapter seam so unit tests can fake Docker below the public boundary.
- Harden the default official repo materializer so public bundles are detached trees without `.git` history.
- Keep all authority flags false: `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced`.

## Testing Decisions

- First RED test is at the adapter public boundary and uses captured parser output, not real Docker.
- Docker pull, container execution, and parser failures are faked below the adapter seam in unit tests.
- The real three-instance Docker attempt is written as packet artifact evidence, not as a unit test.
- Existing official replay, leak-scan, and receipt-validation tests remain in scope for regression checks.

## Out of Scope

- Candidate corpus construction.
- Single-agent baseline generation.
- Reviewer panel changes.
- Autonomous benchmark-to-AutoResearch policy evolution.
- Modifying or replacing the `swebench` dependency used by Verified.

## Further Notes

Reference implementation and benchmark context:

- https://github.com/scaleapi/SWE-bench_Pro-os/tree/ca10a60a5fcae51e6948ffe1485d4153d421e6c5
- https://github.com/scaleapi/SWE-bench_Pro-os/blob/ca10a60a5fcae51e6948ffe1485d4153d421e6c5/swe_bench_pro_eval.py
- https://github.com/scaleapi/SWE-bench_Pro-os/issues/93
- https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro
- https://hub.docker.com/r/jefzda/sweap-images
