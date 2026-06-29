# Tri-Agent Findings

Task id: `pro-runscripts-coverage-20260626`

## Validator A: Coverage

Verdict: revise.

Finding: selected instances are the filtered official replay roster, not just
one adapter call. Only three vendored script directories exist, so batch replay
must preflight the whole selected roster.

Foldback: accepted. The replay runner now preflights after selection and before
prediction/materialization/oracle work.

## Validator B: Fail-Fast Honesty

Verdict: revise.

Finding: the helper reported multiple missing IDs, but `run_swe_bench_pro_oracle`
still only checked one instance at a time. The batch public boundary needed a
test proving no oracle or Docker work starts when selected IDs are missing.

Foldback: accepted. Added a replay-level test that sets
`SWEBENCH_PRO_ORACLE_SCRIPTS_DIR`, selects three instances, and proves both
missing IDs are reported before materializer/oracle calls.

## Validator C: No Generic Script Fabrication

Verdict: revise.

Finding: the implementation had no generic fallback, but tests did not prove
the adapter fails before Docker when scripts are absent or copies scripts
verbatim when present.

Foldback: accepted. Added adapter-level missing-script and verbatim-copy tests,
plus source script hash evidence in receipts.

