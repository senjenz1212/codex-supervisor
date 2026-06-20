# Issues

## Slice 1 - Add guarded live generation report boundary

Priority: P0

Scope: Add a public mergeability live report path that accepts injected generator adapters, refuses to invoke them without `allow_live=true`, records unavailable state for disabled live mode, and preserves report-only invariants.

PRD promise: P1, P2, P6, P7

Public boundary for first RED test: `run_live_mergeability_candidate_generation`

Chosen seam or interface: mergeability bench report interface with fake generator adapters only at the external provider seam.

Acceptance criteria:

- [ ] Disabled live mode returns an unavailable report and does not call either generator adapter.
- [ ] Baseline and supervisor metadata must match model, provider, budget, and timeout before execution.
- [ ] Budget or timeout overrun is recorded as rejected or unavailable, never accepted.
- [ ] Report-only invariants remain false and policy derivation produces no proposal.

## Slice 2 - Generate replayable public-only candidate artifacts for both arms

Priority: P0

Scope: Build public-only task input for baseline and supervisor generation, remove hidden oracle material from worktrees and prompts, create canonical candidate payloads, hash artifacts, and evaluate both generated candidates through the same held-out bench oracle.

PRD promise: P2, P3, P4, P7

Public boundary for first RED test: `run_live_mergeability_candidate_generation`

Chosen seam or interface: mergeability bench live report interface, reusing existing public review and hidden oracle grading functions.

Acceptance criteria:

- [ ] Generator input contains no hidden commands, oracle labels, expected outcomes, protected paths, or hidden fixture content.
- [ ] Each arm records prompt hash, candidate artifact hash, evaluator hash, wall-clock, cost, and token usage when the adapter supplies it.
- [ ] Both arms are graded by the same held-out bench oracle after generation.
- [ ] Candidate artifact hashes are stable across repeated canonical serialization.

## Slice 3 - Replace constant evaluated-path quality flag

Priority: P1

Scope: Derive `candidate_affects_evaluated_path` from changed paths or evaluated-path deltas and reuse the derivation in the AutoResearch evaluator quality manifest.

PRD promise: P5

Public boundary for first RED test: AutoResearch evaluator quality manifest emitted by `run_evaluator_trials`

Chosen seam or interface: evaluator quality manifest surface, with internal helper reuse allowed after the public test exists.

Acceptance criteria:

- [ ] A candidate changing only non-evaluated paths records `candidate_affects_evaluated_path=false`.
- [ ] A candidate changing an evaluated mutable path or producing a meaningful evaluated-path delta records `candidate_affects_evaluated_path=true`.
- [ ] Existing evaluator quality controls remain runtime-native and deterministic.

## Slice 4 - Preserve non-applyable policy boundary for live reports

Priority: P1

Scope: Ensure live generation reports cannot produce applyable policy proposals, gate advancement, default changes, or policy mutation until a separate powered criteria slice changes that authority.

PRD promise: P6, P7

Public boundary for first RED test: policy derivation from the live mergeability report

Chosen seam or interface: existing AutoResearch policy evolution proposal derivation surface.

Acceptance criteria:

- [ ] Live reports expose `metric_applyable=false` and `improvement_claim_allowed=false`.
- [ ] Proposal derivation returns an empty list for live generation reports.
- [ ] Overrun or unavailable arms cannot be interpreted as accepted improvements.
