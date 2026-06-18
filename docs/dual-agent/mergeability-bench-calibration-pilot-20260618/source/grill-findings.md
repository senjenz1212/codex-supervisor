# PRD Grill Findings

### Finding 1: Avoid Treating Corpus Size As Evidence Quality

Status: resolved

The initial goal could be misread as "add twelve cases and therefore trust the benchmark." The PRD now requires positive and negative control coverage per included task, task and candidate hashes in the manifest, and exclusion of tasks that lack both sides of the control pair. This keeps the aggregate report from laundering weak tasks into a strong-looking number.

Resolution: Incorporated into P1, P2, Implementation Decisions, and Testing Decisions.

### Finding 2: Keep Baseline Comparison Paired And Non-Live

Status: resolved

A live model run would add generator variance before the verifier is calibrated. The PRD now makes the pilot operate on a fixed candidate pool and evaluates both arms on identical task and candidate hashes. The baseline arm is intentionally simple, so disagreements explain verification policy rather than model generation skill.

Resolution: Incorporated into P3 and Out of Scope.

### Finding 3: Preserve AutoResearch Truth Boundaries

Status: resolved

The pilot could accidentally become an applyable policy-improvement claim. The PRD now states default_change_allowed, policy_mutated, and gate_advanced must remain false, and no policy proposal should become applyable from this pilot alone.

Resolution: Incorporated into P4 and Testing Decisions.
