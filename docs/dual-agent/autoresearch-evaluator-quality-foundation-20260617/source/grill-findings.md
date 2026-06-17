# PRD Grill Findings: AutoResearch Evaluator Quality Foundation

## Context

The PRD was grilled against the current AutoResearch report-only model, the planning validator contract, public-boundary testing rules, and the observed saturated replay failure where evaluator execution returned identical all-pass trials. Findings below are resolved in the PRD before issue slicing.

### Finding 1: Corpus construction is the load-bearing risk

status: resolved

Risk: The PRD could sound like an evaluator wrapper change while the real difficulty is building held-out controls that discriminate no-op, harmful, and known-good candidate behavior. A saturated corpus would reproduce the current failure.

Resolution: The PRD now makes held-out control behavior a first-class promise, requires no-op, harmful, and known-good candidates, and treats the control corpus as immutable evaluator input.

### Finding 2: Determinism cannot be self-certified

status: resolved

Risk: A report field claiming deterministic evaluator behavior could become another forged trust stamp. That would weaken the same boundary that runtime-native evidence was designed to protect.

Resolution: The PRD requires repeated same-input evaluator execution with normalized output hash comparison. Caller-supplied metadata alone cannot suppress `zero_variance_trials` or permit proposal derivation.

### Finding 3: Report-only authority must remain explicit

status: resolved

Risk: A passing evaluator-quality suite could be mistaken for permission to apply policy, advance a workflow gate, or bypass human approval.

Resolution: The PRD repeats the unchanged authority invariants: `default_change_allowed=false`, `policy_mutated=false`, `gate_advanced=false`, and operator approval remains required for policy evolution.

### Finding 4: Public-boundary tests must cover derivation, not only helpers

status: resolved

Risk: Helper-only tests could prove control math while proposal derivation still accepts saturated reports.

Resolution: The TDD plan starts at `derive_policy_evolution_proposals_from_report`, report validation, and evaluator execution artifacts before helper-level tests.
