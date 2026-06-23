# Implementation Plan

## Report Seam

Use run_paired_acceptance_pilot as the implementation seam. Keep the existing S_probe and S_full accept logic unchanged, and add reviewer-level summaries derived from supervisor_full_gate_reviewer_results after rows are assembled.

## Evidence Additions

Add top-level per_reviewer_arms, configured_reviewer_panel.inter_reviewer_agreement, oracle_isolation, and hidden_field_leak_check to paired_acceptance_report.json. Each reviewer arm reports candidate availability, accept decisions, FAR, TAR, confidence intervals, runtime, model, and per-candidate evidence references.

## Runtime Proof

Run the configured panel fixture measurement with reviewer_panel_mode configured and persist paired_acceptance_report.json under the task directory. Treat any reviewer infrastructure failure as unavailable evidence, not acceptance and not quality rejection.
