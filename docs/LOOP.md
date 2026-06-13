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

## Closeout Measurement

- Transport incidents are observational ledger metrics. AXI submit reattach, AXI catch-up, non-terminal poll, poll failure, and dispatcher lease-reap recoveries are counted into quality-trend details; no metric advances or blocks a gate.
- `codex-supervisor-axi trends` remains TOON-lite by default and exposes the same metrics with `--json` for exact fields. The default output format should only change after the replay corpus shows one format wins both poll-loop turns and emitted bytes.
- Until each format has enough samples, operator/orchestrator poll loops alternate output formats across runs: run 1 uses default TOON-lite `codex-supervisor-axi poll <job_id>`, run 2 uses `codex-supervisor-axi --json poll <job_id>`, and so on until both `format_toon_*` and `format_json_*` trend fields have at least the configured minimum sample count. This alternation is measurement-only and never changes gate authority.
- `visual_evidence_policy=not_required` with non-empty visual/screenshot matches writes `visual_evidence_override_asserted`; sampled P11 audit includes `visual_evidence_override_count` in trend details.
- `codex-supervisor-axi doctor` is read-only. It reports daemon liveness, dispatcher stale leases, AutoResearch runner ticks, weekly P11 audit ticks, draft/runnable experiment counts, pending proposal counts, and help commands for degraded states.
- `codex-supervisor-axi lessons --backfill-ops` idempotently records the operational pre-flight lesson for committing orthogonal fixes with their tests immediately.

## Proof

- Proposal id: `ARP-a32595eaf5c1f694`
- Report hash: `cffb126c6c5ecdf878d38c2ecf75989e6f70876906140168a59e768daaf86879`
- Overlay before hash: `3873dcb319f4070c65b4c9dbc31987f276869941ac785f5843d194bec5ecae4c`
- Overlay after hash: `bd27d04477e0bdc6628802a68a395a9fdb86dd74a6bc31564fa935fe99eb7a16`
- Rollback draft event id: `27`

The proof has exactly two human-touchpoint events: experiment activation event `7` and proposal approval event `23`.
