# Issues

Task id: `pro-corpus-label-stability-20260629`

## Slice 1: Add the report-only label-stability wrapper

PRD promise

P1, P2, P3.

Public boundary

`scripts/swebench_pro_label_stability.py` importable functions and CLI.

Chosen seam

Curated records plus predictions JSONL; live oracle is injected below the wrapper in tests and defaults to the Pro oracle only under `--allow-live`.

Representative prompt/action

Run the wrapper over a Pro predictions JSONL and curated records with `--repeats 3 --allow-live` on the VM.

Allowed outcomes

- Stable candidates are kept unchanged.
- Unstable and unavailable candidates are dropped with explicit reasons.
- A flake report records counts, repeated statuses, and false authority flags.
- CLI refuses live work unless `--allow-live` is present.

Forbidden outcomes

- Relabeling candidates.
- Silent drops.
- Empty stable corpus written as success.
- Secret values in artifacts.
- Any authority flag true.

TDD plan

First RED test: `tests/test_swebench_pro_label_stability.py::test_stable_candidate_is_kept_with_original_label_and_context`.

## 11-Block Tracer Bullets

1. Intent: filter flaky Pro oracle labels before powered benchmark reporting.
2. Actor: benchmark operator running the VM corpus pipeline.
3. Input: curated Pro records and existing oracle-labeled predictions JSONL.
4. Output: stable predictions JSONL and flake report JSON.
5. Public boundary: importable wrapper functions and CLI.
6. First RED: stable candidate kept unchanged with reused oracle context.
7. Live infra: fake oracle in tests; real oracle only under `--allow-live`.
8. Failure mode: empty input or all dropped raises/fails closed.
9. Security: env-var names only; no token values logged or written.
10. Authority: all report-only flags false.
11. Handoff: run on Bokken VM after initial corpus labels exist.
