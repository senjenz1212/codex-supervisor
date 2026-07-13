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
| 7 | LEDGER-001 | after INTEGRITY-A | Implemented; local anchoring only | Tamper-evident append-only evidence ledger |
| 8 | GRADE-001 | after LEDGER-001 | Implemented; locally verified | Immutable grade revisions and invalidation |
| 9 | RUNTIME-001 | after INTEGRITY-B | Implemented; Claude Code and Codex compatibility receipts captured | Provider-neutral agent and model runtimes |
| 10 | TASK-001 | after RUNTIME-001 | Implemented; generic fixtures and one Unity compatibility receipt captured | Task-environment and hidden-verifier adapters |
| 11 | EXP-001 | after GRADE-001 and TASK-001 | Implemented; hermetic execution only | Randomized isolated A/B/C efficacy experiment |
| 12 | TRACER-001 | after EXP-001 | Implemented; hermetic end-to-end trace closes | Cross-slice composition and claim-cap proof |
| 13 | PILOT-001 | after TRACER-001 | **Blocked / not run** | Operational pilot and discordance/flake/cost estimates |
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
