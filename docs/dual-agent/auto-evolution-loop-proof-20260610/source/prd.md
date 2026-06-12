# PRD: Auto-Evolution Loop Proof

## Problem Statement

The supervisor auto-evolution loop now has separate mechanisms for failure-signal drafting, operator activation, daemon execution, accepted-report proposal derivation, operator approval, overlay-attributed trends, weekly audit, and rollback drafting. The program still needs a single proof that these mechanisms work together through the AXI operator surface and fail loudly if a wire is removed.

## Solution

Add a deterministic Phase E proof that drives the complete loop from recurring failure signals through a rollback proposal draft. The proof uses AXI for the two human touchpoints, the daemon task for runnable experiment execution, ledger events for provenance, and committed demo artifacts for replayable operator evidence. The phase does not change gate authority, policy defaults, or acceptance floors.

## User Stories

- As an operator, I can see that recurring failures draft experiments but do not activate them automatically.
- As an operator, I can activate exactly one experiment through `codex-supervisor-axi experiments activate` and later approve exactly one derived proposal through `codex-supervisor-axi approve --proposal-id`.
- As a maintainer, I can remove any major loop wire and get a named failing test that identifies the broken arrow.
- As a reviewer, I can inspect the demo manifest and verify event ids, hashes, trend attribution, and rollback draft evidence.

## PRD Promise Contracts

P1. The proof must start from ledger-backed recurring failure signals and finalization-time experiment drafting. The allowed outcome is exactly one draft experiment with provenance event ids; the forbidden outcome is self-activation or a draft with no provenance.

P2. The proof must have exactly two human touchpoints: AXI experiment activation and AXI proposal approval or denial. The allowed outcome is two operator events; the forbidden outcome is automatic activation, automatic apply, or hidden helper approval.

P3. The proof must execute the activated experiment through the daemon runner and durable evaluator lane. The allowed outcome is an accepted report with evaluator_execution provenance; the forbidden outcome is inline fixture-only metrics.

P4. The accepted report must derive exactly one policy-overlay proposal and approval must apply exactly the recorded diff with before and after hashes. The forbidden outcome is caller-authored candidate_changes or mutation before approval.

P5. The next composed gate instruction must differ after approval and the trend rows must carry the overlay hash and proposal id. The forbidden outcome is an unapplied overlay or unattributed metrics.

P6. A seeded regression window must emit `policy_regression_detected` and draft exactly one rollback proposal without applying rollback automatically. The forbidden outcome is silent regression or self-rollback.

P7. Wire-removal alarms must name T1, T2, T3, T4, T5, and T7 breakages. The forbidden outcome is a vague end-state assertion that hides which wire failed.

P8. The live demo export and `docs/LOOP.md` must be generated from actual proof artifacts. The forbidden outcome is aspirational documentation with unresolved event ids or mismatched hashes.

## Implementation Decisions

The implementation adds `tests/test_auto_evolution_loop.py`, committed demo artifacts, and a narrow generated-attempt baseline metric so accepted live evaluator reports can compute a positive delta. It keeps policy mutation human-approved, keeps `DEFAULT_IMMUTABLE_PATHS` unchanged, and uses existing AXI and daemon task entrypoints instead of inventing a second orchestrator.

## Testing Decisions

The first test exercises the public loop boundary: finalization, AXI activation, daemon tick, accepted report, AXI approval, overlay instruction composition, trend attribution, weekly audit scheduling, and rollback drafting. Negative tests disable each major wire and assert the named break stage. Artifact tests verify committed demo hashes and event references.

## Out of Scope

This phase does not change gate predicates, reviewer policy, default policy-overlay content, dispatcher semantics, AutoResearch acceptance thresholds, or any product workflow outside the supervisor auto-evolution proof.
