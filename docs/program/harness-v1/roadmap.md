# Harness v1 Roadmap

The detailed execution prompts remain in
`docs/harness-v1-execution-plan-and-prompts-20260711.md`. This file records the
program-level dependency order.

| Order | Slice | Gate | Program result |
|---:|---|---|---|
| 1 | PROGRAM-001 | now | Charter, claim ladder, derived ClaimGate, forbidden claims |
| 2 | INTEGRITY-A | now, parallel | Terminal CAS, immutable audits, symlink-safe writes |
| 3 | INTEGRITY-B | now, parallel | Process-group cancellation and stale-worker cleanup |
| 4 | OBS-001 | now | Normalized events, run joins, semantic drift |
| 5 | REPLAY-001 | now | Strict schemas and recorded-checkout replay |
| 6 | TRACE-001 | after PROGRAM-001 | Typed versioned trace graph and closure gate |
| 7 | LEDGER-001 | after INTEGRITY-A | Tamper-evident append-only evidence ledger |
| 8 | GRADE-001 | after LEDGER-001 | Immutable grade revisions and invalidation |
| 9 | RUNTIME-001 | after INTEGRITY-B | Provider-neutral agent and model runtimes |
| 10 | TASK-001 | after RUNTIME-001 | Task-environment and hidden-verifier adapters |
| 11 | EXP-001 | after GRADE-001 and TASK-001 | Randomized isolated A/B/C efficacy experiment |
| 12 | PILOT-001 | after EXP-001 | Operational pilot and discordance/flake/cost estimates |
| 13 | CONFIRM → OPT → DEPLOY → SCALE | pilot-gated | Confirmation, optimization, guarded deployment, scale decision |

## Pilot-Gated Parameters

Confirmation sample size, arm ceilings, optimization budget, and scale
criteria remain unset until PILOT-001 produces observed discordance, flake, and
cost estimates. Fixed values before that point would be false precision.

## Advancement Rule

A later slice may reference an earlier artifact but cannot inherit a higher
claim level by reference alone. Its own evidence bundle must satisfy
`claim-ladder.yaml`.
