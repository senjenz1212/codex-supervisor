# Issue Pack

## Issue 1: Fire Fixture Mergeability Measurement With Panel And Produced Baseline

## What to build

Add a public runner that builds replayable fixture single-agent baseline receipts, invokes the configured reviewer panel through the existing mergeability full-gate seam, exports the paired acceptance report, and annotates the primary comparison as `supervisor_vs_single_agent_baseline`.

## PRD promise

Promises covered: P1, P2, P3, P4, P5.

Public boundary for first RED test: `run_fixture_panel_produced_baseline_measurement`.

Chosen seam or interface: the fixture measurement runner in `supervisor.mergeability_bench`, delegating into `run_paired_acceptance_pilot`.

Representative action: call the runner on `tests/fixtures/mergeability_bench` with injected configured reviewer adapters and an output directory.

Allowed outcomes: report artifact is persisted, S_full and single-agent baseline are available, the primary comparison uses `single_agent_baseline`, legacy metadata remains non-primary, and report-only flags stay false.

Forbidden outcomes: unavailable reviewer panel accepted as success, missing baseline receipts counted as reject or accept, metadata accept-all labeled primary, oracle material in reviewer packets, or any policy mutation.

## Acceptance criteria

- [ ] The runner writes `paired_acceptance_report.json` with `primary_comparison_name=supervisor_vs_single_agent_baseline`.
- [ ] `supervisor_full_gate.availability_status` and `single_agent_baseline.availability_status` are available for a successful measurement.
- [ ] Per-candidate rows include reviewer ids, packet hashes, panel decisions, and rationale evidence.
- [ ] Public-floor rejects short-circuit as available full-gate rejects; public-floor accepts still require reviewer verdicts.
- [ ] An unavailable configured panel raises a measurement error and does not produce a false success.
- [ ] Metadata accept-all remains present only as legacy non-primary calibration evidence.
- [ ] Calibration report-only flags remain false and no policy proposal is derivable.

## Blocked by

None - can start immediately.
