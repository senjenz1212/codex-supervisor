# PRD Grill Findings

## Summary

The PRD was grilled against the repo's existing agentic eval report boundary,
the dormant fan-out policy default, and the dual-agent workflow replay rules.
All findings are resolved before issue slicing.

### Finding 1: "Real gated workflow" conflicts with "no live calls by default"

Status: resolved

- Risk: A literal live invocation would violate deterministic replay and could
  contact providers.
- Resolution: The runner's default mode is `fixture_replay`: it consumes
  cassette workflow outcomes shaped like the real `run_dual_agent_workflow`
  result, including the full gate sequence, P-probe statuses, and reviewer-panel
  decisions. A non-default live/workflow-runner mode must be explicitly enabled
  and is not used by CI or fixtures.

### Finding 2: Equal budget must compare total budget, not per-worker budget

Status: resolved

- Risk: Fan-out could appear better by receiving multiple worker budgets.
- Resolution: Each row records `token_budget` and `budget_usd_limit`; the runner
  validates the full per-task budget tuple across `lead_direct`,
  `agentic_allowed`, and `agentic_required` before scoring.

### Finding 3: Scoring must not become a second reviewer panel

Status: resolved

- Risk: Coupling eval scoring to production reviewer decisions would blur
  boundaries.
- Resolution: Scoring is a deterministic eval-only decision tree over declared
  required verdicts and evidence refs. It does not call or configure production
  reviewers.

### Finding 4: Report-only invariant needs a regression test

Status: resolved

- Risk: A future runner might write config or flip policy while creating a
  comparison.
- Resolution: Tests assert `default_change_allowed=false`, policy snapshots
  remain `off`, and no config path is written.

### Finding 5: Graceful degradation must be explicit

Status: resolved

- Risk: The comparison could omit the operator-facing "does this fail well?"
  dimension.
- Resolution: Rows include a deterministic `graceful_degradation` metric derived
  from score and gate rejection state unless explicitly provided by fixture
  metrics.
