# Issues: PILOT-001

## PL1: Close readiness prerequisites

- PRD promises: P3, P6
- Public boundary: pilot readiness validator
- Blocked by: Phase 0 plus TRACE/LEDGER/GRADE/RUNTIME/TASK/EXP/TRACER
- Acceptance:
  - Required focused/regression suites are green on a commit-pinned tree.
  - TRACER-001 has a complete generic and Unity thread capped at L2.
  - External runtime/verifier availability and budget are authorized.

## PL2: Freeze pilot protocol and roster

- PRD promises: P1, P2, P3
- Public boundary: immutable pilot manifest
- Blocked by: PL1
- Acceptance:
  - Unique task roster and exclusions are frozen and hashed.
  - Confirmation reservation proves disjointness.
  - Task count, arm budgets, assignment version/key custody, models, images,
    verifiers, retry/failure rules, and stop rule are frozen before launch.

## PL3: Execute the pilot

- PRD promises: P3, P4
- Public boundary: A/B/C pilot runner
- Blocked by: PL2
- Acceptance:
  - Every planned task has one A/B/C row or an explicit ITT/common-infra status.
  - No selective rerun, hidden exclusion, or cross-arm state sharing occurs.
  - Raw hashes, grades, ledger checks, trace closure, costs, and latencies are
    retained.

## PL4: Freeze readout and confirmation derivation

- PRD promises: P4, P5, P6
- Public boundary: immutable pilot readout
- Blocked by: PL3
- Acceptance:
  - Publish discordance/flake/infra/cost/latency estimates and uncertainty.
  - Freeze the exact confirmation task/run count from the preregistered
    alternative and observed discordance.
  - Explicitly refuse causal and ROI claims.
