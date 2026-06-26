# Next Supervisor Prompts

These prompts are sequenced. Do not run Prompt 2 until Prompt 1 has produced a real or blocked AEB-0 artifact.

## Prompt 1: Implement / Run AEB-0 Real Official All-Arms Artifact Gate

```text
You are the supervisor agent for /Users/sam.zhang/Documents/codex-supervisor.

Goal: implement and run AEB-0: Real Official All-Arms Artifact Gate for docs/dual-agent/auto-evolve-benchmark-promotion-bridge-20260625.

Use PRD -> TDD. Load prd-to-tdd and child skills before acting.

Critical constraints:
- Do not close subagents.
- Preserve all report-only authority flags false:
  - metric_applyable=false
  - improvement_claim_allowed=false
  - powered_improvement_claim_allowed=false
  - human_mergeability_claim_allowed=false
  - default_change_allowed=false
  - policy_mutated=false
  - gate_advanced=false
- Produce a real official_all_arms_diagnostic_report.json, or produce a blocked AEB-0 artifact with exact unavailable reasons.
- Pin serious claims to SWE-bench Pro or held-out equivalent. Verified is smoke-only unless explicitly justified.
- Missing CLI prerequisites must produce blocked AEB-0 evidence rather than silent pre-artifact exits.
- Empty-but-present FAIL_TO_PASS/PASS_TO_PASS buckets may pass. Missing or malformed buckets must be unavailable/blocked.
- Hidden oracle/protected material must not reach public/reviewer/generator/frozen/public packet surfaces.

First RED tests:
1. No real all-arms artifact -> downstream bridge readiness blocked.
2. Missing CLI prerequisites -> blocked AEB-0 artifact/ledger event.
3. Missing produced baseline receipt -> blocked.
4. Missing official oracle receipt -> blocked.
5. Verified dataset -> smoke-only readiness.
6. Artifact preserves all false authority flags.

Output:
- changed files
- tests run
- AEB-0 artifact path, or blocked artifact path
- exact blocked reasons if blocked
- next resume command if live benchmark could not complete
```

## Prompt 2: Langfuse/Opik Read-Only Sink Slice 1

Run only after Prompt 1 has produced a real or blocked AEB-0 artifact.

```text
You are the supervisor agent for /Users/sam.zhang/Documents/codex-supervisor.

Goal: create a separate read-only observability sink packet for existing AEB-0 report artifacts. This is not part of the benchmark trust path.

Use PRD -> TDD. Do not implement trust-path changes. Do not close subagents.

Scope:
- Compare Langfuse and Opik only for read-only artifact ingest.
- Prefer Langfuse if the project chooses LiteLLM-native tracing and can accept ClickHouse as net-new infra; prefer Opik only if the project chooses its eval/annotation workflow first.
- The sink may ingest existing AEB-0 artifacts, blocked artifacts, powered reports, and ledger rows.
- The sink must never affect:
  - metric_applyable
  - improvement_claim_allowed
  - powered_improvement_claim_allowed
  - human_mergeability_claim_allowed
  - policy_mutated
  - gate_advanced
  - effective_vote_estimate
  - roster selection
  - evaluator_run_ref/hash
  - policy derivation

First RED tests:
1. Sink refuses to ingest without AEB-0 artifact ref.
2. Sink preserves false authority flags from source artifact.
3. Sink payload cannot set applyability/promotion/mutation flags.
4. Sink traces/scores/annotations/dashboard URLs cannot satisfy evaluator provenance, quality controls, empty-floor win, or candidate overlay requirements.
5. Sink annotations cannot change reviewer roster, pairwise oracle-error overlap, or effective-vote status.

Output:
- separate packet path
- PRD/issues/TDD/audit for read-only sink
- chosen sink recommendation with tradeoffs
- explicit sink-never-in-trust-path invariants
```

## Deferred Prompt: Maintainer-Merge Annotation Queue

Do not run yet.

Reason: the annotation queue introduces a new human-label workflow and maintainer-merge rubric. It should wait until AEB-0 exists and the read-only sink boundary has proven it can ingest artifacts without entering the trust path.
