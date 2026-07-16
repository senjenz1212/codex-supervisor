# Harness v1 Roadmap

The detailed execution prompts remain in
`docs/harness-v1-execution-plan-and-prompts-20260711.md`. This file records the
program-level dependency order. The current verification receipt is
`verification-20260713.md`.

| Order | Slice | Gate | Current status | Program result |
|---:|---|---|---|---|
| 1 | PROGRAM-001 | now | Implemented; locally verified | Charter, claim ladder, derived ClaimGate, forbidden claims |
| 2 | INTEGRITY-A | now, parallel | Implemented; locally verified | Terminal CAS, immutable audits, symlink-safe writes |
| 3 | INTEGRITY-B | now, parallel | Implemented; locally verified | Process-group cancellation and stale-worker cleanup |
| 4 | OBS-001 | now | Implemented; locally verified | Normalized events, run joins, semantic drift |
| 5 | REPLAY-001 | now | Implemented; locally verified | Strict schemas and recorded-checkout replay |
| 6 | TRACE-001 | after PROGRAM-001 | Implemented; locally verified | Typed versioned trace graph and closure gate |
| 7 | LEDGER-001-HERMETIC | after INTEGRITY-A | Implemented for the registered evidence projections; local anchoring only | Tamper-evident append-only ledger, directly signed artifact-manifest attestations, and deterministic rebuilds for the registry in `projection-registry.yaml` |
| 7b | LEDGER-001-OPERATIONAL | deployment prerequisite | **Blocked / not run** | Rollback-independent external anchor and production signing identity |
| 8 | GRADE-001 | after LEDGER-001-HERMETIC | Implemented; locally verified | Immutable grade revisions, invalidation lineage, historical replay, and current-authority blocking |
| 9 | RUNTIME-001-HERMETIC | after INTEGRITY-B | Implemented; Claude Code and Codex compatibility receipts captured | Provider-neutral agent/model seams and local contract tests |
| 9b | RUNTIME-001-OPERATIONAL | authorization prerequisite | **Blocked / not run as a coding matrix** | Real generic and Unity coding tasks through both provider runtimes |
| 10 | TASK-001-HERMETIC | after RUNTIME-001-HERMETIC | Implemented; generic fixtures and one Unity compatibility receipt captured | Task-environment, immutable verifier-execution specification, and hidden-verifier adapters |
| 10b | TASK-001-OFFICIAL | official harness prerequisite | **Partially implemented / no retained live grade** | Local crash-recoverable verification journal and experiment-resume binding implemented; live resumable official-harness execution, rollback-independent checkpoint, and retained oracle evidence remain blocked |
| 11 | EXP-001 | after GRADE-001 and TASK-001 | Implemented; hermetic execution only | Randomized isolated A/B/C efficacy-experiment kernel |
| 12 | TRACER-001-HERMETIC | after EXP-001 | Implemented; L1 end-to-end trace closes | Cross-slice composition and claim-cap proof with deterministic transports |
| 12b | TRACER-001-OPERATIONAL | after RUNTIME-001-OPERATIONAL and TASK-001-OFFICIAL | **Blocked / not run** | Real generic + Unity × Claude + Codex × A/B/C matrix with independent verification |
| 13 | PILOT-001 | after TRACER-001-OPERATIONAL | **Blocked / not run** | Operational pilot and discordance/flake/cost estimates |
| 14 | CONFIRM → OPT → DEPLOY → SCALE | pilot-gated | **Not eligible** | Confirmation, optimization, guarded deployment, scale decision |

## Pilot-Gated Parameters

Confirmation sample size, arm ceilings, optimization budget, and scale
criteria remain unset until PILOT-001 produces observed discordance, flake, and
cost estimates. Fixed values before that point would be false precision.

## Advancement Rule

A later slice may reference an earlier artifact but cannot inherit a higher
claim level by reference alone. Its own evidence bundle must satisfy
`claim-ladder.yaml`.

Implementation status is not efficacy status. The completed local and
operational compatibility checks do not show that the Supervisor arm beats a
compute-matched direct arm. Until PILOT-001 and CONFIRM-001 produce disjoint,
blinded, task-level evidence, the program must not claim causal improvement,
positive ROI, portability, or safe auto-improvement.

`LEDGER-001-HERMETIC` does not claim that every table in the Supervisor
database is event-sourced. Its exact rebuildability scope is the
machine-readable `projection-registry.yaml`; unlisted materializations remain
outside the claim.
