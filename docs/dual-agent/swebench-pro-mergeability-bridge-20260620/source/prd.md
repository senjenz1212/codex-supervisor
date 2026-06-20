## Problem Statement

Supervisor currently has two related but separate evaluation surfaces. The local mergeability bench can measure false accepts, true accepts, reviewer-panel deltas, and report-only guardrails, but its tasks are curated fixtures rather than real benchmark issues. The SWE-bench Pro pilot adapter uses real benchmark instance identifiers and solve-rate style reports, but it measures pass-at-k uplift rather than whether the supervisor accepts fewer bad candidate patches. Operators need a bridge that can evaluate supervisor gate discrimination on real SWE-bench Pro-shaped tasks without exposing hidden oracle material to generators, public probes, reviewers, transcripts, or receipts.

## Solution

Add a SWE-bench Pro mergeability bridge that converts instance metadata and candidate patches into public-only mergeability packets, freezes baseline, S_probe, and S_full decisions, and only then attaches held-out FAIL_TO_PASS/PASS_TO_PASS oracle results for aggregate FAR/TAR/FRR reporting. The first S_probe substrate will be a static public probe: patch applicability against a public checkout plus explicitly curated public lint/build commands when a fixture or manifest supplies them. It will not use FAIL_TO_PASS, PASS_TO_PASS, test_patch, hidden command strings, final scores, or oracle labels. S_full will compose S_probe with an independent reviewer panel over a public-only reviewer packet.

## User Stories

1. As a supervisor operator, I want SWE-bench Pro tasks evaluated through the mergeability FAR/TAR path, so that real benchmark issues can calibrate false-accept risk.
2. As an AutoResearch operator, I want hidden oracle tests withheld until arm decisions are frozen, so that supervisor decisions cannot be circularly coupled to the answer key.
3. As a reviewer-panel maintainer, I want public reviewer packets to exclude FAIL_TO_PASS, PASS_TO_PASS, test_patch, final_score, and oracle_accept, so that reviewer judgments remain independently auditable.
4. As an evaluation reader, I want S_probe and S_full reported separately, so that deterministic public checks and reviewer-panel marginal value are not collapsed into one opaque number.
5. As a policy steward, I want all bridge outputs to remain report-only, so that calibration evidence cannot mutate policy or advance gates before a later powered promotion slice.
6. As a benchmark maintainer, I want the existing SWE-bench pass-at-k adapter preserved, so that solve-rate uplift and supervisor false-accept reduction stay distinct instruments.
7. As an incident reviewer, I want oracle-isolation failures to produce red or unavailable rows, so that leaked hidden material never becomes accepted evidence.

## PRD Promise Contracts

P1. SWE-bench Pro instances are converted into mergeability-compatible public task packets without exposing oracle-only fields.
P2. S_probe public-probe substrate is explicitly defined as static patch applicability plus curated public lint/build commands, tested, and reported rather than silently assumed.
P3. Hidden FAIL_TO_PASS/PASS_TO_PASS oracle data is used only after baseline, S_probe, and S_full arm decisions are fixed.
P4. S_full includes the independent reviewer panel over a public-only packet, and reviewer unavailability makes S_full unavailable rather than accepted.
P5. Reports include FAR, TAR, FRR, matched-TAR status, sample denominators, confidence intervals, and oracle-isolation proof.
P6. All outputs remain report-only with improvement_claim_allowed=false, default_change_allowed=false, policy_mutated=false, and gate_advanced=false.
P7. Existing SWE-bench solve-rate reporting remains available and is not confused with mergeability FAR/TAR reporting.

## Implementation Decisions

The bridge should be a deep module with a small public interface, separate from the existing SWE-bench pass-at-k report builder. A likely seam is a new report builder that accepts SWE-bench Pro-shaped instance records, arm candidate artifacts, public-probe configuration, reviewer-panel results, and post-decision oracle outcomes. Existing mergeability helpers for arm summaries, Wilson confidence intervals, oracle-isolation scanning, reviewer packet construction patterns, and report-only guardrails should be reused where practical. The bridge should not require live SWE-bench Pro execution in this slice; fixtures can model public metadata, candidate patches, and hidden oracle outcomes.

## Testing Decisions

The first RED tests must exercise the bridge report builder and public packet builder rather than helper-only internals. Tests should prove that public packets and reviewer packets exclude oracle fields, that oracle results cannot be attached before arm decisions, that S_probe substrate metadata is present in every report, and that S_full reviewer unavailability remains unavailable rather than accepted. Existing tests for the SWE-bench pass-at-k report and mergeability report-only behavior should remain green to prove this bridge does not rewrite the solve-rate adapter or weaken current mergeability invariants.

## Out of Scope

This slice does not run live SWE-bench Pro jobs, does not purchase or access commercial held-out splits, does not implement a FrontierCode-style maintainer rubric, and does not create applyable policy proposals. It does not change default supervisor gates, reviewer aggregation policy, AutoResearch promotion criteria, or the existing pass-at-k SWE-bench pilot. It only creates an oracle-isolated measurement bridge and fixture-backed proof that real-task shaped records can flow through the mergeability estimand.
