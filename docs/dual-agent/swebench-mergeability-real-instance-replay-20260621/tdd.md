# TDD Plan

## Public Boundary Test 1: Replay Manifest To Report

RED: Add a test that writes a replay manifest with one SWE-bench-shaped instance, one public bundle, one candidate `model_patch`, and hidden oracle commands. Invoke `swebench_mergeability_replay_runner` and assert the report contains denominators, excludes hidden fields from public packets, and records report-only invariants.

GREEN: Implement manifest loading, public bundle copying, patch text application, freeze-before-oracle ordering, and bridge report wiring through the existing runner helpers.

## Public Boundary Test 2: Deterministic Patch Apply Failure

RED: Add a replay test with an invalid patch text and assert the candidate report records `patch_apply_failure`, S_probe rejects, oracle still labels after freeze, and the runner does not crash.

GREEN: Add a patch-text apply receipt path that returns structured success/failure and preserves existing `patch_operations` behavior.

## Public Boundary Test 3: Static/Lint-Only Substrate Label

RED: Add a replay test with no public commands and assert the report labels S_probe as static/lint-only, not public-test-backed.

GREEN: Add explicit substrate detail to runner reports and derive the label from public command availability.

## Public Boundary Test 4: CLI Replay Command

RED: Invoke the CLI script with a replay manifest and output directory, then assert stdout points to the report and the report contains unavailable S_full when no panel is configured.

GREEN: Add a no-live CLI entry point over the replay runner and optionally expose it as a project script.

## Public Boundary Test 5: Reviewer Panel Availability

RED: Run replay with no reviewer panel and assert S_full is unavailable; run with an injected panel in-process and assert S_full can accept or reject independently from S_probe.

GREEN: Reuse the existing strict panel decision path and configured reviewer-panel adapter hook without defaulting missing review to accept.
