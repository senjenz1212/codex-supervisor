# TDD Plan: REPLAY-001

This plan distinguishes current tests from unexecuted future RED/GREEN work.
It is not a skill receipt or a record of external replay execution.

## Public-Boundary Tracer Bullets

1. Schema declaration
   - Existing test:
     `test_replay_schema_versions_reject_missing_schema_declaration`.
   - Then cover current aliases, a named migration, and an unknown future
     version through the same public function.

2. Recorded-checkout isolation
   - Existing test:
     `test_p11_regrade_uses_hashed_immutable_snapshot_after_live_tree_changes`.
   - Assert the second audit retains the frozen overlay hash after the live
     artifact is removed.

3. Missing checkout
   - Existing test:
     `test_weekly_p11_audit_preserves_incompatible_status_without_recorded_checkout`.
   - Forbidden outcome: silently reading `Path.cwd()` or the workflow's live
     checkout.

4. Resolved model route
   - Existing tests in `tests/test_run_manifest.py`.
   - A `default` alias must produce a recorded route while preserving
     `exact_model_identity=false` when the exact serving model is unknown.

5. Complete manifest
   - Next RED: build provenance with every required component category and
     assert `status=complete`, stable hashes, and strict schema compatibility.
   - Negative case: remove one category and assert `status=incomplete`.

6. Ledger-compatible audit fixtures
   - Next RED: run replay and quality-trend tests together without direct
     `UPDATE events` fixture mutations.

## Verification Commands

```text
.venv/bin/python -m pytest -q \
  tests/test_version_drift_replay.py \
  tests/test_replay_strict.py \
  tests/test_run_manifest.py
```

Broader regression gate:

```text
.venv/bin/python -m pytest -q tests/test_quality_trends.py
```
