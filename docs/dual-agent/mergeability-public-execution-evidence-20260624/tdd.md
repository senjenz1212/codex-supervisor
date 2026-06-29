# TDD Plan

## Cycle 1: Full-Gate Packet Evidence Boundary

RED: Add a public-boundary test asserting `run_paired_acceptance_pilot` emits `public_execution_evidence` in each full-gate reviewer packet.

GREEN: Add a packet evidence helper that summarizes the existing public review receipts without rerunning commands.

## Cycle 2: Reverse-Classical Evidence

RED: Add a public-boundary test for the known-good fixture candidate showing candidate tests pass on patched code and fail on original code through the reverse-classical command status.

GREEN: Derive `reverse_classical_test_quality` from existing `public_candidate_test` and `public_reverse_test` command results.

## Cycle 3: SWE-Bench Packet Evidence

RED: Add a public-boundary test reading the SWE-bench reviewer packet artifact and asserting patch-apply and public-probe evidence are present.

GREEN: Add a SWE-bench evidence helper that uses existing patch-apply receipts and public command receipts.

## Cycle 4: Oracle Isolation

RED: Existing packet leak tests must continue to reject forbidden oracle material.

GREEN: Avoid exact hidden oracle key strings inside the evidence block and rely on the existing packet leak scanner.

