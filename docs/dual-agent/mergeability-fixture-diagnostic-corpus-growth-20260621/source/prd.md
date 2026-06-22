## Problem Statement

Slice 1A fired the fixture measurement instrument and produced an honest calibration result, but it exposed a diagnostic weakness instead of a promotion-ready win. The public floor preserved all three oracle-positive cases while reducing many false accepts, and the full supervisor gate rejected every candidate after panel review. That makes the panel marginal comparison not matched because true-accept rate fell from one to zero on only three positive examples. The operator needs a corpus growth slice driven by that measured failure mode, not by generic benchmark expansion.

## Solution

Grow the fixture corpus with diagnostic oracle-positive candidates that exercise the same public packet, produced baseline, public floor, panel, and hidden oracle path used by Slice 1A. Preserve negative controls and false-accept traps, but bias the next authored additions toward positive fixtures because the current blocker is true-accept loss and wide positive-denominator intervals. Re-run the fixture measurement and report n_good, n_bad, S_probe false accepts, S_full false accepts, true-accept loss, confidence intervals, and matched-TAR status without allowing the report to become applyable policy evidence.

## User Stories

1. As an operator, I want the next corpus additions to follow directly from Slice 1A's measured panel true-accept loss, so that we stop adding fixtures that do not answer the current blocker.
2. As a supervisor evaluator, I want enough oracle-positive examples to diagnose whether reviewer-panel strictness is systematic, so that a not-matched full-gate comparison becomes interpretable.
3. As a policy owner, I want calibration reports to keep false-accept and true-accept denominators visible, so that a lower false-accept rate cannot hide an unacceptable true-accept collapse.
4. As a reviewer, I want public packets for new fixtures to preserve hidden oracle isolation, so that panel decisions never see final scores, hidden tests, or protected paths.
5. As a future benchmark maintainer, I want the fixture growth rationale recorded with the corpus, so that later slices can distinguish diagnostic calibration from powered held-out evidence.

## PRD Promise Contracts

P1. The fixture corpus growth is explicitly keyed to the Slice 1A result: full-gate matched TAR was not matched because S_full true-accept rate was zero while S_probe true-accept rate was one.
P2. The slice adds diagnostic oracle-positive fixture coverage while preserving at least one positive control, one negative control, and one public-pass hidden-fail false-accept trap.
P3. New fixture candidates preserve the public/hidden split: reviewer packets and public worktrees exclude hidden oracle paths, final_score, oracle_accept, expected outcomes, and protected artifacts.
P4. The measurement report records n_good, n_bad, S_probe accepted false accepts, S_full accepted false accepts, TAR loss, matched-TAR status, and confidence intervals.
P5. Calibration output remains report-only with metric_applyable=false, improvement_claim_allowed=false, policy_mutated=false, gate_advanced=false, and no best-of-K or in-sample peak labeled as improvement.

## Implementation Decisions

Use the existing mergeability bench module as the deep module and keep the public seam at the fixture measurement runner/report interface. Add fixture candidates in the existing authored-corpus shape rather than creating a separate corpus type. The preferred additions are valid fixes across current task families, plus enough retained negative traps to ensure the report still exposes false accepts. The report should carry a diagnostic label or rationale tying the added cases to the Slice 1A full-gate not-matched result.

## Testing Decisions

The first RED test must exercise the public measurement boundary and show that the grown corpus increases oracle-positive diagnostic coverage while retaining report-only invariants. Follow-up tests should verify hidden oracle isolation for new candidates, preserved controls, reported denominator fields, TAR-loss visibility, and rejection of any attempt to label calibration as a held-out improvement. Helper tests may support manifest validation only after the public report behavior is covered.

## Out of Scope

This slice does not tune reviewer prompts, weaken panel strictness, change gate authority, fetch SWE-bench data, generate live candidates, or promote any policy change. It does not claim that the supervisor improves production outcomes. It also does not solve statistical power; it creates a better diagnostic fixture corpus for the next measurement pass.
