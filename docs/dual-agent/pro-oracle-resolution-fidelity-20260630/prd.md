# Pro Oracle Resolution Fidelity PRD

Task id: `pro-oracle-resolution-fidelity-20260630`

Depends on: `pro-corpus-powered-scale-run-20260629`

## Problem Statement

Phase 0 curation for the powered SWE-bench Pro scale run correctly blocked solver spend, but the exclusion reasons show a fidelity problem rather than a thin pool problem. The local Pro oracle treats any empty `PASS_TO_PASS` bucket as unavailable, while upstream SWE-bench resolution semantics treat an empty regression bucket as a vacuous pass when `FAIL_TO_PASS` exists and passes. That makes runnable gold instances look unrunnable and forces premature pool expansion.

There is a second curation mismatch: dry-gold curation rejects instances solely because `test_command_return_code != 0`, even when the parser produced non-empty tests and both `fail_to_pass_status` and `pass_to_pass_status` are `pass`. The return code should stay visible as provenance, but parsed resolved status is the resolution signal for this curation gate.

Direct upstream reference: [SWE-bench grading.py](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/grading.py)

## Solution

Bring the Pro oracle and dry-gold curation gate into alignment with SWE-bench resolution semantics before any solver/model spend. Empty `PASS_TO_PASS` becomes `pass_to_pass_status="pass"` only when `FAIL_TO_PASS` is non-empty and passes, and the result carries `pass_to_pass_empty_vacuous_pass=true`. Empty `FAIL_TO_PASS` remains oracle-unavailable. Dry-gold curation accepts parsed resolved gold when the patch applied, tests were parsed, and both status buckets are `pass`, even if the test command returned non-zero; it records `rc_nonzero_resolved=true`.

Thread those disclosures through the adapter, bridge/corpus rows, summaries, powered report metadata, and Definition-of-Done evidence. Then rerun Phase 0 curation on the same 108-record pool only. Do not start solver/model spend unless the prereg reliability gate passes and the operator approves the next phase.

## PRD Promise Contracts

### P1. Empty PASS_TO_PASS is upstream-faithful vacuous pass.

Public boundary

`run_swe_bench_pro_oracle(context)`.

Chosen seam

The Pro scoring branch after parser output is non-empty and the hidden `FAIL_TO_PASS` / `PASS_TO_PASS` lists have been read from oracle context.

Allowed outcomes

- When `FAIL_TO_PASS` is non-empty and every fail-to-pass test is parsed as passed, `PASS_TO_PASS=[]` yields `pass_to_pass_status="pass"`.
- The oracle result and adapter receipt expose `pass_to_pass_empty_vacuous_pass=true`.
- Oracle-good may be produced only with that disclosure present.

Forbidden outcomes

- Treating empty `PASS_TO_PASS` as `oracle_unavailable`.
- Silently accepting an empty `PASS_TO_PASS` bucket without a disclosure field.
- Letting the disclosure imply maintainer mergeability or any authority flag.

### P2. FAIL_TO_PASS remains required.

Public boundary

`run_swe_bench_pro_oracle(context)`.

Chosen seam

The same Pro scoring branch, before status computation.

Allowed outcomes

- Missing or empty `FAIL_TO_PASS` returns `oracle_unavailable=true`.
- The unavailable reason remains explicit: `pro_oracle_bucket_empty:fail_to_pass`.

Forbidden outcomes

- Accepting empty `FAIL_TO_PASS`.
- Labeling a candidate good from `PASS_TO_PASS` alone.
- Collapsing empty `FAIL_TO_PASS` into the vacuous-pass path.

### P3. Non-empty PASS_TO_PASS regressions still fail.

Public boundary

`run_swe_bench_pro_oracle(context)`.

Chosen seam

The set-inclusion status computation for non-empty `PASS_TO_PASS` buckets.

Allowed outcomes

- A non-empty `PASS_TO_PASS` bucket with a missing or failed regression test yields `pass_to_pass_status="fail"`.
- The candidate is not oracle-good.

Forbidden outcomes

- Treating all `PASS_TO_PASS` evidence as vacuous.
- Hiding a non-empty regression failure under the new disclosure field.

### P4. Nonzero rc does not override parsed resolved statuses.

Public boundary

`scripts/swebench_pro_batch_driver.py::_oracle_gold_runnable(result)`.

Chosen seam

The dry-gold curation decision over the normalized oracle receipt.

Allowed outcomes

- `patch_applied=true`, `tests_count>0`, `fail_to_pass_status="pass"`, and `pass_to_pass_status="pass"` is runnable even when `test_command_return_code != 0`.
- The returned details retain `test_command_return_code` and set `rc_nonzero_resolved=true`.

Forbidden outcomes

- Counting rc-only resolved gold as unrunnable.
- Dropping or overwriting the non-zero return-code provenance.
- Accepting non-zero rc when parsed tests are empty, the patch did not apply, or either status is not `pass`.

### P5. Disclosure survives adapter -> bridge/corpus/report.

Public boundary

`_interpret_oracle_outcome`, `build_swe_bench_pro_candidate_corpus`, `summarize_swe_bench_pro_candidate_corpus`, `swebench_mergeability_powered_factorial_runner`, and the powered real benchmark DoD checker.

Chosen seam

Copy disclosure fields from raw oracle outcomes and receipts into interpreted outcomes, prediction rows, corpus summary counts, powered report metadata, and DoD evidence.

Allowed outcomes

- Prediction rows carry `pass_to_pass_empty_vacuous_pass` and `rc_nonzero_resolved` when either applies.
- Corpus summaries and powered reports expose `vacuous_pass_to_pass_count` and `rc_nonzero_resolved_count`.
- The DoD checker includes those counts in evidence and still keeps authority flags false.

Forbidden outcomes

- Hidden disclosure flags.
- Calling the result stronger than `swe_bench_held_out_test_pass_proxy`.
- Treating disclosures as blockers by themselves when the prereg thresholds and report-only labels are satisfied.

### P6. Re-curation remains gated by the prereg reliability bar.

Public boundary

The Phase 0 curation artifacts for `powered-scale-20260630T0200Z`.

Chosen seam

Rerun dry-gold curation only on the existing 108-record pool after the code fix; inspect `curated-roster.json` and `phase0-gate-decision.json`.

Allowed outcomes

- If `curated_instances >= 100`, `unique_repos >= 5`, top repo share `<= 50%`, and k=8 plausibly clears `n >= 100/arm` plus `>=50` discordant-pair reach, stop for operator review before solver spend.
- If the bar still fails, record blocked evidence and only then expand the pool.

Forbidden outcomes

- Starting solver/model spend automatically.
- Allowing batch-driver solver/model spend without a passed Phase 0 gate artifact.
- Lowering the prereg bar after seeing the rerun.
- Reporting the rerun as a powered benchmark.

### P7. Solver spend is mechanically interlocked on Phase 0 approval.

Public boundary

`scripts/swebench_pro_batch_driver.py` CLI/driver phase transition.

Chosen seam

Before `run_solver_batch(...)` can execute, the driver must read an explicit Phase 0 gate decision artifact and require `solver_spend_allowed=true`.

Allowed outcomes

- Dry-gold curation can run without a spend-allowed artifact.
- Solver/model phases fail closed unless a gate artifact explicitly permits spend.
- The gate artifact path and decision are recorded in the batch manifest.

Forbidden outcomes

- `--allow-live --run-solver` bypassing Phase 0 gate approval.
- Spending model budget because a human forgot to omit `--run-solver`.
- Treating minimum CLI thresholds as a substitute for the prereg reliability gate.

## User Stories

1. As a benchmark operator, I want empty `PASS_TO_PASS` to match SWE-bench resolution semantics, so that valid Pro gold instances are not excluded by a local-only stricter guard.
2. As a benchmark operator, I want empty `FAIL_TO_PASS` to remain unavailable, so that a candidate cannot be called good without a bug-fix signal.
3. As a benchmark operator, I want non-empty regression failures to remain failures, so that vacuous-pass handling does not weaken the regression oracle.
4. As a benchmark operator, I want rc-nonzero-but-resolved dry-gold runs accepted with disclosure, so that parser-derived resolved evidence is not discarded.
5. As a benchmark reviewer, I want vacuous-pass and rc-nonzero counts visible in reports, so that the powered artifact can be judged with full caveats.
6. As an auto-evolve maintainer, I want all authority flags false, so that benchmark evidence cannot mutate policy or defaults.
7. As a scale-run operator, I want Phase 0 rerun on the existing pool before expansion, so that we spend compute only after fixing the known curation bug.
8. As a scale-run operator, I want solver spend mechanically blocked until Phase 0 passes, so that accidental CLI flags cannot start model work.

## Implementation Decisions

- Keep the scoring fix inside the Pro oracle adapter. This is the deep interface already invoked by curation, corpus labeling, and label-stability wrappers.
- Add disclosure fields as first-class report fields rather than burying them inside receipt tails.
- Keep `FAIL_TO_PASS` fail-closed; it is not equivalent to the upstream vacuous regression bucket.
- Keep rc-nonzero acceptance local to the dry-gold curation predicate; the raw return code remains in the receipt and curation details.
- Reuse existing corpus summary and powered runner seams for metadata counts instead of introducing another reporting surface.
- Add a Phase 0 gate-decision path to the batch driver for solver/model phases. Curation remains runnable without it; solver/model spend does not.
- Align `pro_oracle_gold_proof_runner` with the new disclosed resolution semantics if its manifest remains operator evidence.
- Do not call Docker, live model runners, reviewer panels, or powered factorial from unit tests.

## Testing Decisions

- First RED: `tests/test_swe_bench_pro_oracle.py::test_empty_pass_to_pass_is_vacuous_pass_with_disclosure`.
- Tests fake Docker below `run_swe_bench_pro_oracle`; they still exercise the public adapter function.
- Batch-driver tests call `_oracle_gold_runnable` and `build_and_label_corpus` with fake oracle outputs below the public curation/labeling seam.
- Corpus/report tests assert disclosure propagation through JSONL loader, summary, powered runner metadata, and DoD evidence.
- Live Phase 0 rerun is execution evidence, not a unit test.

## Out of Scope

- Solver/model execution.
- Reviewer panel execution.
- Powered factorial execution at scale.
- Auto-evolve, benchmark promotion, or policy/default mutation.
- Claiming maintainer mergeability.

## Further Notes

The expected immediate result is a larger curated roster from the same 108-record pool. That is still Phase 0 evidence only. Solver spend remains blocked until the prereg reliability gate passes and the operator explicitly approves the next phase.
