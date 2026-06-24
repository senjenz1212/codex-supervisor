# PRD Grill Findings

## Finding 1: Fixture ablation can be useful and still invalid for roster selection

Status: resolved in PRD.

The PRD now separates diagnostic execution from roster-selection authority. Fixture runs may report metrics and cost, but the guard must block any roster decision until real or disagreement-enriched candidate evidence exists.

## Finding 2: Codex-only calibration could be confused with full-panel evidence

Status: resolved in PRD.

The PRD requires Codex-only evidence to remain under `codex_only_calibration_panel`, with `full_panel_evidence_allowed=false` and no policy proposal authority.

## Finding 3: Cache behavior needs to be visible, not merely implemented

Status: resolved in PRD.

The existing checkpoint identity already includes reviewer roster ids and option hash. The report must expose a cache policy so readers know changed rosters recompute reviewer work or require a valid identity match.

## Finding 4: Real benchmark readiness is not the same as fixture guardrails

Status: resolved in PRD.

The PRD does not claim to run a real benchmark. It records the evidence gate that a future real/disagreement roster report must satisfy.
