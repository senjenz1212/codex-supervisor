# Issue: SWE-bench Real-Instance Replay Runner

## PRD Promise

Claims: P1, P2, P3.

Public boundary: `swebench_mergeability_replay_runner` and the replay CLI command.

First RED test: invoke the replay runner on a temporary checked-in-style replay manifest containing public source files, protected hidden oracle files, and a candidate `model_patch` unified diff. Assert hidden fields are absent from public packets, patch application is recorded, frozen decisions are written before oracle labels, and the report remains non-applyable.

## What To Build

Build a deterministic replay path for SWE-bench-shaped bundles. The runner should load manifest rows, copy public bundle files into isolated worktrees, apply model patch text artifacts, run public static/lint probes, freeze arm decisions, run hidden oracle commands only after freeze, and feed the resulting decisions into the existing SWE-bench mergeability bridge report. Add a CLI command so operators can run the replay without importing Python internals.

## Acceptance Criteria

- [ ] Public packets and reviewer packets exclude `patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, oracle labels, and protected path content.
- [ ] Patch text or `model_patch` applies successfully or records a deterministic apply failure without crashing the replay.
- [ ] Frozen decisions are written before oracle output artifacts are created.
- [ ] S_probe reports a static/lint-only substrate when the replay has no public tests.
- [ ] S_full is unavailable when the reviewer panel is unavailable, and is evaluated through the injected/configured panel when available.
- [ ] CLI replay writes a JSON report with n_good/n_bad denominators and report-only invariants.

## Blocked By

None. This slice builds on the existing bridge and fixture runner seams.
