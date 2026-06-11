# TDD Grill Findings

### Finding 1: Public Boundary Must Avoid The Lower-Level Helper

status: resolved
Resolution: the first test must call `derive_policy_evolution_proposals_from_report`, not `create_policy_evolution_proposals`, so Phase C proves caller-authored candidate changes are no longer required.

### Finding 2: Non-Overlay Must Be Rejected Before Proposal Creation

status: resolved
Resolution: the non-overlay test checks for zero proposal-created events and zero file mutation, not merely an apply-time exception.

### Finding 3: Metric Delta Must Be In The Assertion

status: resolved
Resolution: the happy-path test asserts metric before, metric after, and metric delta in the derivation block so provenance is replayable.

### Finding 4: Draft-Only Invariants Must Be Repeated

status: resolved
Resolution: tests assert both proposal flags and unchanged overlay bytes.
