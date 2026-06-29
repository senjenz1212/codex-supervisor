# TDD Grill Findings

Task id: `powered-real-benchmark-run-20260626`

## Verdict

Accepted. The TDD plan starts at the public checker seam and keeps live infrastructure below the boundary.

## Findings

### T1. Do not TDD the scaled run.

Finding: a test that runs the Pro oracle, solver, or panel would be brittle, slow, and dishonest on this host.

Resolution: the live run is recorded as execution evidence only; unit tests validate the DoD checker with report-shaped fixtures.

### T2. Helper-only tests would miss the artifact contract.

Finding: private helper tests could pass while the composed report still lacks all-arms labels or authority flags.

Resolution: every requested RED test invokes the public checker interface.

### T3. Positive tests are not enough.

Finding: a checker can be tautological if it only accepts the happy fixture.

Resolution: add `test_underpowered_artifact_is_rejected` to prove fail-closed behavior.

### T4. Do not reject diagnostic powered readiness as an authority flag.

Finding: Slice 6 intentionally uses `powered_metric_applyable` diagnostically while keeping `metric_applyable=false`.

Resolution: the checker treats `powered_metric_applyable` as evidence and still requires every authority flag false.
