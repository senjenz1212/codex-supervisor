## Slice 1: Budget gate and public generator inputs

PRD promise: P1, P2

Priority: high

Scope: Add an official live runner that refuses before loader, materializer, or generator side effects unless allow_live is true and max_budget_usd is positive, then constructs public-only matched generator inputs.

Acceptance criteria:
- [ ] allow_live false raises or returns unavailable before generators run.
- [ ] non-positive max_budget_usd raises or returns unavailable before generators run.
- [ ] baseline and supervisor inputs share model, provider, budget, timeout, prompt hash, and public instance fields.
- [ ] hidden oracle fields are absent from generator input JSON.

## Slice 2: Prediction artifact replay through official oracle

PRD promise: P3, P5

Priority: high

Scope: Persist generated model patches as prediction JSONL artifacts, then evaluate them by calling the official replay adapter with the same loader, materializer, reviewer panel, and oracle adapter.

Acceptance criteria:
- [ ] generated predictions contain stable candidate ids and model_patch text.
- [ ] official replay report is embedded in the live report.
- [ ] frozen decision files predate oracle outputs through the replay adapter.
- [ ] candidate hashes, prompt hashes, evaluator hashes, wall-clock, token usage, and costs are recorded.

## Slice 3: Budget overrun and report-only guardrails

PRD promise: P4, P5

Priority: high

Scope: Treat cost overruns and malformed generator results as unavailable live evidence, not accepted or applyable policy evidence.

Acceptance criteria:
- [ ] over-budget runs return status unavailable and gaming flag budget_exceeded.
- [ ] unavailable runs do not invoke official replay after the overrun.
- [ ] metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced stay false.
- [ ] existing SWE-bench mergeability replay and official replay tests remain green.
