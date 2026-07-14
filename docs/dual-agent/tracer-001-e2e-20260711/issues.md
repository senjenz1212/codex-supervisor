# Issues: TRACER-001

## TR1: Freeze tracer inputs and matrix

- PRD promises: P1, P3
- Public boundary: tracer run manifest
- Blocked by: RUNTIME-001, TASK-001, EXP-001
- Acceptance:
  - Generic and Unity tasks, revisions, images, verifiers, models, budgets, and
    assignment version are pinned.
  - Tracer task IDs are reserved from pilot/confirmation sets.
  - Twelve arm executions are enumerated before launch.

## TR2: Execute generic end-to-end thread

- PRD promises: P1-P5
- Public boundary: tracer runner
- Blocked by: TR1, OBS-001, TRACE-001, LEDGER-001, GRADE-001
- Acceptance:
  - All A/B/C arms on both runtimes terminate.
  - Events join, result freezes, verifier is blind, grade appends, ledger
    verifies, trace closes, and ClaimGate returns exactly L1 for the
    same-principal fixture.

## TR3: Execute Unity end-to-end thread

- PRD promises: P1-P5
- Public boundary: tracer runner
- Blocked by: TR2 plus an available pinned Unity verifier environment
- Acceptance:
  - Same guarantees as TR2 using a real Unity project and verifier.
  - Result/grade schemas match the generic thread.

## TR4: Publish the evidence bundle

- PRD promises: P2-P5
- Public boundary: immutable tracer report
- Blocked by: TR2, TR3
- Acceptance:
  - Report lists every run/artifact/grade/trace/ledger hash and failure.
  - No missing arm or selective exclusion is hidden.
  - Claim text explicitly caps hermetic authority at L1.
