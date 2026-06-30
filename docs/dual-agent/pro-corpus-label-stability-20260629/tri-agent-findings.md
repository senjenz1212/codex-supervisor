# Tri-Agent Findings

Task id: `pro-corpus-label-stability-20260629`

Exactly three independent read-only reviewers were used.

## Reviewer A: Contract Correctness

Verdict: revise, then accepted after fold-back.

Finding: `patch_applied` evidence was too loose. Missing evidence and string `"false"` could classify repeated `(pass, pass)` runs as stable, unlike the existing Pro corpus builder's `patch_applied is True` requirement.

Fold-back: `scripts/swebench_pro_label_stability.py` now normalizes patch-apply evidence like the existing Pro corpus path and treats anything other than `True` as unavailable. `tests/test_swebench_pro_label_stability.py::test_missing_or_string_false_patch_apply_evidence_is_unavailable` covers the regression.

## Reviewer B: Live Safety And Secrets

Verdict: revise, then accepted after fold-back.

Finding: Stable output copied whole prediction rows, so arbitrary token-bearing fields could leak. The report also serialized raw oracle unavailable reasons.

Fold-back: stable rows now fail closed on secret-like keys or values before writing, and oracle unavailable reasons are slugged or redacted before report serialization. Tests cover secret-bearing prediction rows and secret-bearing oracle reasons.

## Reviewer C: Packet, Pin, And Scope Hygiene

Verdict: revise, then accepted after fold-back.

Findings: report-only flag coverage was overclaimed, and a direct public `classify_stability(...)` call without `expected_repeats` could return `STABLE`.

Fold-back: the CLI report test now asserts all authority flags false and `labels.report_only is True`; `classify_stability(...)` now returns `UNAVAILABLE` when run summaries lack a positive expected repeat count.
