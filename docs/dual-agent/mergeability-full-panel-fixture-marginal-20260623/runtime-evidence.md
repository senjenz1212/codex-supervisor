# Runtime Evidence

## Completed Bounded Diagnostic

The committed paired_acceptance_report.json was produced by run_paired_acceptance_pilot with reviewer_panel_mode configured on two public-accepted fixture candidates: known-good and hidden-behavior-miss. Both configured reviewers returned verdicts, supervisor_full_gate availability was available, full_roster_available_count was 2, and unavailable_full_gate_count was 0. The run remained report-only with metric_applyable, improvement_claim_allowed, policy_mutated, and gate_advanced all false.

## Marginal Status

panel_marginal_delta_at_matched_true_accept is not computed in this artifact. The report honestly marks status insufficient_candidate_pool with reason requires_at_least_two_oracle_positive_candidates because the bounded diagnostic has one oracle-positive candidate and one oracle-negative candidate.

## Corpus-Scale Attempt

An all-corpus configured reviewer run was attempted first, but synchronous real reviewer execution entered long serial reviewer calls and was interrupted before producing paired_acceptance_report.json. That attempt is not counted as completed evidence and is not represented by the committed report. The next hardening step is a bounded or parallel corpus runner with explicit per-candidate wall-clock controls before treating full-corpus configured-panel measurement as a reliable command.
