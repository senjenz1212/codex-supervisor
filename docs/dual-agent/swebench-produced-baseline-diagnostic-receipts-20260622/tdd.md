# TDD Plan

## Boundary First

The first RED tests exercise the official SWE-bench replay and live runner boundaries, because the user-visible behavior is the diagnostic report and not the internal receipt resolver. Helper-level receipt normalization is trusted only after report rows and arm summaries expose the expected evidence.

### test_official_replay_gold_smoke_without_produced_baseline_receipt_is_unavailable

Maps to: Slice 1, P1, P4

RED: Run official replay with a `gold-smoke` prediction that has model patch text but no produced baseline receipt, then assert the baseline arm is unavailable and matched-TAR returns `baseline_arm_unavailable`.

GREEN: Preserve missing receipt normalization through official replay candidate construction and frozen decision rows.

### test_official_replay_produced_baseline_receipt_populates_baseline_arm

Maps to: Slice 1, P2

RED: Run official replay with a valid replayed single-agent baseline receipt and assert accept decision, evidence kind, decision source, artifact hash, and baseline arm availability are recorded.

GREEN: Forward candidate-level produced receipt keys from predictions into replay candidates and bridge arm summaries.

### test_official_replay_hash_mismatched_baseline_receipt_is_unavailable

Maps to: Slice 1, P3, P4

RED: Run official replay with a produced baseline receipt whose artifact hash does not match the candidate patch and assert the row is unavailable with hash-mismatch evidence.

GREEN: Reuse the shared produced-baseline resolver for official replay candidates before FAR/TAR summaries are computed.

### test_official_live_generates_matched_arms_and_reuses_official_replay

Maps to: Slice 2, P2, P3, P4

RED: Run the official live runner with a baseline generator accept decision and assert the downstream bridge row contains a replayable produced baseline receipt rather than an untrusted decision source.

GREEN: Synthesize official live baseline receipts with the trusted `single_agent_candidate_generation` source and preserve the official live runner label in producer metadata.

### test_official_live_ignores_supervisor_supplied_baseline_receipts

Maps to: Slice 2, P5

RED: Run the official live runner with a supervisor generator that returns a matching-hash `single_agent_baseline_decision`, then assert the supervisor-produced row remains baseline-unavailable.

GREEN: Gate explicit baseline receipt forwarding to the baseline arm before writing generated predictions or replay candidates.
