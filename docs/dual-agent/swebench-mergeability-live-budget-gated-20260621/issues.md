# Issue: Budget-Gated Live SWE-bench Mergeability Runner

## PRD Promise

Claims: P1, P2, P3.

Public boundary: `swebench_mergeability_live_runner` and the SWE-bench mergeability CLI live mode.

First RED test: invoke the live runner with fake generator adapters and `allow_live=false`, then assert the runner refuses before any generator call and emits no policy-applyable result.

## What To Build

Build a live generation path for SWE-bench mergeability replay bundles. The runner should derive public-only generator inputs, call matched baseline and supervisor generator adapters only when live and budget gates pass, write generated patch artifacts into a replay manifest, delegate evaluation to the replay runner, and export report-only live receipts. Add CLI live flags that make the approved live path explicit.

## Acceptance Criteria

- [ ] Live generation refuses without `allow_live=true`.
- [ ] Live generation refuses without `max_budget_usd > 0`.
- [ ] Baseline and supervisor generation arms record matched provider, model, timeout, and budget.
- [ ] Hidden oracle keys, protected files, and oracle labels are absent from generator inputs and reviewer packets.
- [ ] Budget overrun marks the live run unavailable and does not count as accepted.
- [ ] Oracle execution happens through replay after frozen decisions are written.
- [ ] Live report keeps `metric_applyable`, `improvement_claim_allowed`, `policy_mutated`, and `gate_advanced` false.

## Blocked By

None. The replay runner from the prior slice provides the evaluation substrate.
