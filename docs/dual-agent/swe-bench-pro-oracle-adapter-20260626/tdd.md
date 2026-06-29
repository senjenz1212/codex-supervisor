# TDD Plan

Task id: `swe-bench-pro-oracle-adapter-20260626`

## Public Boundary

The first implementation proof uses `run_swe_bench_pro_oracle(context)` as the callable passed into the existing `oracle_runner` seam. Docker, parser files, and subprocesses are faked below that seam in unit tests. The real Docker attempt is artifact evidence, not a unit test.

## Cycle 1: Parser Output To Pass Status

RED

Add `tests/test_swe_bench_pro_oracle.py::test_pro_runner_returns_pass_status_on_gold_fixture`. It injects captured parser-style JSON containing all required fail-to-pass and pass-to-pass test names with `PASSED` status, fakes Docker below the adapter, and expects `fail_to_pass_status="pass"`, `pass_to_pass_status="pass"`, and a receipt.

Minimal GREEN

Implement Pro list parsing, parser-output loading, subset classification, and receipt construction.

## Cycle 2: Fail Closed On Runtime Failure

RED

Add `tests/test_swe_bench_pro_oracle.py::test_pro_runner_unavailable_on_pull_or_parse_failure`. It fakes a failed `docker pull` or missing parser output and expects `oracle_unavailable=True` plus a stage-specific `oracle_unavailable_reason`.

Minimal GREEN

Wrap Docker and parse failures in unavailable results; do not raise through the public boundary for ordinary adapter failures.

## Cycle 3: Explicit Fail Classifications

RED

Extend `tests/test_swe_bench_pro_oracle.py` with parser-output cases for `fail/pass` and `pass/fail`. Missing required fail-to-pass tests must yield `fail_to_pass_status="fail"`, and missing required pass-to-pass tests must yield `pass_to_pass_status="fail"`.

Minimal GREEN

Classify both label sets independently from parser output. Do not collapse all failures into unavailable when parser output is well-formed.

## Cycle 4: Bridge Interpretation Contract

RED

Add `tests/test_swe_bench_pro_oracle.py::test_pro_runner_outcome_feeds_interpret_contract`. It feeds the Pro adapter mapping into `_normalise_oracle_adapter_outcome(...)` and `_interpret_oracle_outcome(...)` through the same status keys the replay runner consumes.

Minimal GREEN

Conform the Pro adapter result to the existing bridge contract and keep unavailable status compatible with `_interpret_oracle_outcome(...)`.

## Cycle 5: Pro Row Fields Stay Post-Freeze

RED

Add a replay-level test that records the adapter context and scans frozen decisions/reviewer packet text for lowercase and uppercase Pro oracle fields.

Minimal GREEN

Normalize and pass Pro fields through `_official_hidden_oracle(...)` and `adapter_context`, while expanding the hidden-field scan for lowercase Pro keys.

## Cycle 6: Materialized Public Bundle Has No Git History

RED

Add a test that materializes a local fixture repo with future refs/tags and asserts the returned bundle has no `.git` directory.

Minimal GREEN

Remove `.git` after checkout in the default materializer and treat that detached tree as the public bundle.

## Cycle 7: Real Three-Instance Artifact

RED

Not a unit test. The packet artifact is missing before execution.

Minimal GREEN

Run the Pro adapter on the three existing AEB-0 selected instances with pinned scripts and record either completed statuses or honest unavailable stages under `artifacts/`.

## Refactor Check

- Keep the Pro implementation in the existing official oracle adapter module.
- Do not modify the Verified adapter behavior.
- Keep Docker command construction small and auditable.
- Keep all authority flags false.

## Accepted Ledger Record

```jsonl
{"stage":"tdd_review","task_id":"swe-bench-pro-oracle-adapter-20260626","status":"accepted","first_red":"tests/test_swe_bench_pro_oracle.py::test_pro_runner_returns_pass_status_on_gold_fixture","evidence":["docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/tdd.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
