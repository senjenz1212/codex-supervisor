## Slice 1: Official Replay CLI Requires Oracle Runner

Priority: P0

Scope: Add the public CLI behavior that refuses official replay metrics without an explicit oracle adapter and passes the configured oracle runner into the official replay runner when present.

Acceptance Criteria:
- [ ] CLI official replay without oracle adapter fails before metrics are emitted.
- [ ] CLI and direct runner official replay with an unknown adapter kind fail before metrics are emitted.
- [ ] CLI official replay with a fake official adapter writes official_replay_report.json through the injected runner seam.
- [ ] Official-labeled adapters without harness metadata, artifact paths, and status-bearing receipts mark the report unavailable.
- [ ] Official-labeled adapters with invalid receipt proof suppress FAR/TAR in the top-level report, nested replay reports, instance reports, and CLI summary.
- [ ] The report keeps metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced false.

PRD promise: P1, P4, P5

## Slice 2: Filtered Instance Selection Before Prediction Coverage

Priority: P0

Scope: Apply repeatable instance-id selection and deterministic limits before validating prediction coverage, then record the selected rows and filter rationale in the replay manifest.

Acceptance Criteria:
- [ ] Repeatable --instance-id filters dataset rows and predictions for non-selected rows are not required.
- [ ] --limit selects rows deterministically and records selection metadata.
- [ ] Prediction coverage failures reference only selected instances.

PRD promise: P2

## Slice 3: Frozen Public Decisions Before Oracle Receipts

Priority: P0

Scope: Ensure public packets and frozen decisions exclude hidden oracle fields, then write oracle receipts only after the frozen-decision artifact exists.

Acceptance Criteria:
- [ ] Hidden oracle material is absent from public packets and frozen decisions.
- [ ] Oracle receipts are written after frozen_decisions_path exists.
- [ ] Official-equivalent label validation failure marks the run unavailable rather than accepted.

PRD promise: P3, P4, P5
