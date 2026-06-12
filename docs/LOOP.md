# Supervisor Auto-Evolution Loop

This document is generated from the Phase E proof manifest at `docs/dual-agent/auto-evolution-loop-proof-20260610/demo-manifest.json`. It describes the live loop as implemented and demonstrated, not a future design.

## Loop

1. Recurring supervisor failure signals are recorded in the ledger.
2. Workflow finalization drafts an inert AutoResearch experiment with provenance.
3. Human touchpoint 1: the operator runs `codex-supervisor-axi experiments activate <experiment_id>`.
4. The daemon runner executes runnable experiments through the durable evaluator lane, capped by `max_runnable_experiments_per_week`.
5. Accepted reports derive draft policy-overlay proposals; no policy is applied automatically.
6. Human touchpoint 2: the operator runs `codex-supervisor-axi approve --proposal-id <proposal_id>` or `codex-supervisor-axi deny --proposal-id <proposal_id>`.
7. Approved overlays affect subsequent gate instruction composition and are attributed in trend rows.
8. The weekly audit cadence `p11_audit_cadence_s` records observational P11 audit events.
9. Regression windows draft rollback proposals; rollback still requires operator approval.

## Proof

- Proposal id: `ARP-a32595eaf5c1f694`
- Report hash: `cffb126c6c5ecdf878d38c2ecf75989e6f70876906140168a59e768daaf86879`
- Overlay before hash: `3873dcb319f4070c65b4c9dbc31987f276869941ac785f5843d194bec5ecae4c`
- Overlay after hash: `bd27d04477e0bdc6628802a68a395a9fdb86dd74a6bc31564fa935fe99eb7a16`
- Rollback draft event id: `27`

The proof has exactly two human-touchpoint events: experiment activation event `7` and proposal approval event `23`.
