# TDD Grill Findings

Task id: `pro-corpus-label-stability-20260629`

## Finding 1: The first test must hit the wrapper boundary, not a helper.

Status: resolved.

Resolution: The first RED test calls `repeat_oracle_labels(...)` and `filter_stable_corpus(...)` and verifies stable output plus Pro context, rather than unit-testing private normalization alone.

## Finding 2: Fake oracle must stay below the boundary.

Status: resolved.

Resolution: Tests inject the fake oracle runner through the public repeat function or monkeypatch the CLI default before `main(...)`; no test calls live Docker or model APIs.

## Finding 3: Report-only authority needs direct coverage.

Status: resolved.

Resolution: The pin artifact and report builder set every authority flag false; the tests cover output contents and the focused suite exercises report creation through the CLI.

## Finding 4: The all-dropped case needs a hard failure.

Status: resolved.

Resolution: The TDD plan includes an explicit empty/all-dropped test and the implementation raises/fails closed.

## Finding 5: Stable classification must require patch-apply evidence.

Status: resolved.

Resolution: Reviewer A found that missing or string-false `patch_applied` evidence could be accepted as stable. The implementation now mirrors the Pro corpus builder's normalization and treats anything other than `patch_applied is True` as unavailable.

## Finding 6: Outputs must reject or sanitize secret-bearing input.

Status: resolved.

Resolution: Reviewer B found that arbitrary prediction fields and raw oracle unavailable reasons could leak token values. Stable rows now fail closed on secret-like keys or values, and oracle unavailable reasons are slugged or redacted before report serialization.

## Finding 7: Public classifier calls must not infer repeat count.

Status: resolved.

Resolution: Reviewer C found that a direct public call with no `expected_repeats` could classify one run as stable. `classify_stability(...)` now returns `UNAVAILABLE` with `missing_expected_repeats` unless run summaries carry a positive expected repeat count.
