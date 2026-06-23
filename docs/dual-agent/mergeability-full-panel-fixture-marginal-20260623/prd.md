## Problem Statement

The mergeability calibration can now launch both configured reviewers, but it still needs an artifacted fixture run proving that the full roster produces supervisor_full_gate evidence rather than falling back to partial or imputed decisions. The current measurement question is deliberately narrow: can the configured panel run on public-only fixture packets, expose each reviewer separately, and report the panel marginal over S_probe without claiming production improvement.

## Solution

Run the existing fixture corpus through run_paired_acceptance_pilot with reviewer_panel_mode configured, require both independent-reviewer-0 Cursor and independent-reviewer-1 Codex verdicts, and persist paired_acceptance_report.json in the task evidence directory. The report will keep S_probe, conservative S_full, and individual reviewer arms distinct, include inter-reviewer agreement, and mark the panel marginal computed only when matched true-accept conditions are satisfied. Every output remains calibration-only and report-only.

## User Stories

1. As an evaluation operator, I want the configured reviewer panel to produce full-roster evidence, so that I can tell whether Cursor and Codex are actually participating.
2. As a supervisor maintainer, I want per-reviewer FAR and TAR arms, so that reviewer marginal behavior is visible before any aggregate claim is trusted.
3. As a benchmark designer, I want oracle-isolation checks on reviewer packets, so that hidden oracle material cannot leak into public decisions.
4. As a gate reviewer, I want missing reviewer verdicts to make S_full unavailable, so that partial panels never masquerade as full-panel acceptance.
5. As a research lead, I want computed-or-honest panel marginal status, so that an unmatched TAR result is not transformed into a headline gain.
6. As an operator, I want committed runtime evidence and tests, so that the fixture calibration can be audited without rerunning live reviewers.

## PRD Promise Contracts

P1. With both configured reviewers available, a configured full-panel fixture run produces supervisor_full_gate available rows, configured_reviewer_panel.full_roster_available_count greater than zero, and a persisted paired_acceptance_report.json.
P2. Hidden oracle material stays outside reviewer packets and generator/public packet surfaces; oracle_isolation.ok and hidden_field_leak_check.ok must pass for accepted evidence.
P3. The report emits per-reviewer arms for each configured reviewer, including per-candidate decisions, FAR, TAR, denominators, availability counts, and inter-reviewer agreement.
P4. panel_marginal_delta_at_matched_true_accept is computed with a numeric delta only when S_probe and S_full true-accept rates match, otherwise it reports not_matched or unavailable with a typed reason.
P5. Cursor reviewer worktree isolation must not raise cursor_modified_worktree, and reviewer infrastructure failure must be labeled unavailable or panel_missing_verdict_block rather than quality rejection or acceptance.
P6. The fixture report remains non-applyable: metric_applyable, improvement_claim_allowed, policy_mutated, and gate_advanced all stay false.

## Implementation Decisions

The report seam remains run_paired_acceptance_pilot because that is the public measurement interface used by fixture calibration and existing paired acceptance consumers. The implementation adds a deep report helper that derives per-reviewer arms from supervisor_full_gate_reviewer_results, preserving existing S_probe and S_full decision logic. Inter-reviewer agreement is derived only from available reviewer verdicts on shared candidates. Reviewer packet leak status is summarized at the report boundary from the existing oracle reference scanner.

## Testing Decisions

Testing starts at the paired report boundary rather than helper-only seams. The public-boundary tests cover full-roster availability, per-reviewer arms, Cursor isolation diagnostics, computed-or-honest marginal status, infrastructure failure classification, and report-only oracle isolation. Focused regression coverage runs the mergeability bench, Cursor agent, dual-agent slice zero, and reviewer panel aggregation suites. The produced report itself becomes a runtime evidence artifact after tests pass.

## Out of Scope

This slice does not run a real SWE-bench benchmark, fetch live benchmark instances, generate new candidate patches, promote policy changes, or claim human maintainer mergeability. It does not treat Codex-only reviewer evidence as full-panel evidence, and it does not tune the reviewer roster or benchmark thresholds from the fixture result. Powered live evaluation remains a separate program step.
