# Issues: REPLAY-001

## R1: Fail closed on schema drift

- PRD promises: P1
- Public boundary: `check_replay_schema_versions`
- Blocked by: none
- Acceptance:
  - Missing schema declarations are incompatible.
  - Unknown future schemas are incompatible.
  - Current aliases and named migrations remain deterministic.

## R2: Isolate historical evaluation

- PRD promises: P2, P3
- Public boundary: `run_sampled_p11_false_accept_audit`
- Blocked by: R1
- Acceptance:
  - A live-tree change cannot alter a recorded audit result.
  - Dirty recorded results require a hash-valid immutable overlay.
  - Missing or unavailable recorded checkout evidence fails closed.
  - Rerun, regrade, and replay contracts remain distinct.

## R3: Complete the provenance manifest

- PRD promises: P4
- Public boundary: `build_execution_provenance`
- Blocked by: R1
- Acceptance:
  - A lane requested as `default` records a resolved route.
  - Required execution component categories are hashed or marked missing.
  - `status=complete` is impossible with unresolved models or missing
    components.
  - Exported manifests pass strict schema compatibility.

## R4: Restore cross-slice regression compatibility

- PRD promises: P2, P3
- Public boundary: quality-trend audit APIs
- Blocked by: append-only ledger integration
- Acceptance:
  - Quality-trend tests seed timestamps without mutating immutable events.
  - Replay-focused and quality-trend suites pass together.
