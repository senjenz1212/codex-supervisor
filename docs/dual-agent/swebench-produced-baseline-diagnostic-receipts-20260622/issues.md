# Issues

## Slice 1: Enforce produced baseline receipts in SWE-bench diagnostics

Priority: P0

Scope: Drive official replay through candidate rows with missing, valid, and hash-mismatched baseline receipts. Ensure the report records unavailable reasons, provenance fields, arm availability, and matched-TAR refusal through the public SWE-bench report interface.

PRD promise: P1, P2, P3, P4

Public boundary for first RED test: `swebench_mergeability_official_replay_runner`

Chosen seam or interface: official replay report rows and bridge arm summaries emitted after decision freeze

Acceptance criteria:

- [ ] Gold-smoke predictions without produced baseline receipts keep the single-agent baseline unavailable.
- [ ] Valid produced baseline receipts populate baseline accept, source, artifact hash, producer, and evidence kind.
- [ ] Hash-mismatched receipts become unavailable with `candidate_artifact_hash_mismatch`.
- [ ] Matched-TAR comparisons return `baseline_arm_unavailable` when baseline evidence is unavailable.

## Slice 2: Preserve produced baseline provenance in live official diagnostics

Priority: P0

Scope: When the official live baseline generator returns an accept or reject decision, synthesize a produced baseline receipt that uses a trusted decision source and records the official live runner label, prompt hash, model, provider, and candidate artifact hash.

PRD promise: P2, P3, P4, P5

Public boundary for first RED test: `swebench_mergeability_official_live_runner`

Chosen seam or interface: official live report generated predictions and downstream official replay bridge rows

Acceptance criteria:

- [ ] Baseline generator accept decisions produce replayable baseline receipt rows.
- [ ] The receipt uses a trusted decision source accepted by the shared resolver.
- [ ] Official live runner provenance remains visible through the producer runner label.
- [ ] Supervisor-arm receipt fields are ignored and do not populate the single-agent baseline arm.
- [ ] Report-only invariants remain false for policy mutation and improvement claims.
