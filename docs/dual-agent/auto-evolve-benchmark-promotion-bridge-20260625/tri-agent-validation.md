# Tri-Agent Validation Plan

Exactly three validation subagents were spawned and left open for the AEB-0 revision.

## Agent A: Dependency And AutoResearch Gate Auditor

Agent id: `019f01be-9d8c-7503-89fe-2863a660ae03`.

Prompt:

```text
You are Agent A: Dependency and AutoResearch gate auditor for /Users/sam.zhang/Documents/codex-supervisor. Do not edit files. Do not close any agents. Validate the planned packet revision: AEB-0 must become the root gate before bridge/policy proposal readiness; benchmark reports stay report-only; bridge emits AutoResearch records only after qualified benchmark evidence. Check docs/dual-agent/auto-evolve-benchmark-promotion-bridge-20260625 plus supervisor/autoresearch/policy_evolution.py, validation.py, report.py, orchestrator.py, and relevant SWE/mergeability report-only flags. Return concise findings with exact file:line refs and any required packet changes.
```

Validation question:

Does the packet make AEB-0 the root dependency for bridge and policy proposal readiness, while keeping benchmark reports report-only and preserving AutoResearch as the only policy-evolution input path?

## Agent B: SWE-Bench / All-Arms Artifact Auditor

Agent id: `019f01be-d349-7263-894d-4fc2f5bebaa6`.

Prompt:

```text
You are Agent B: SWE-bench/all-arms artifact auditor for /Users/sam.zhang/Documents/codex-supervisor. Do not edit files. Do not close any agents. Validate AEB-0 requirements against official all-arms runner, official oracle adapter, baseline receipts, CLI gates, and leak checks. Special focus: empty-but-present FAIL_TO_PASS/PASS_TO_PASS buckets may pass, but missing/malformed buckets must block/unavailable. Return concise findings with exact file:line refs and required packet changes.
```

Validation question:

Does AEB-0 cover real artifact generation, pre-artifact CLI blockers, produced baseline receipts, official oracle receipts, S_probe/S_full/oracle/reviewer availability, leak checks, and precise oracle bucket semantics?

## Agent C: Observability / Sink Isolation Auditor

Agent id: `019f01be-ff3f-7870-89c7-7143a3b6d8a0`.

Prompt:

```text
You are Agent C: Observability/sink isolation auditor for /Users/sam.zhang/Documents/codex-supervisor. Do not edit files. Do not close any agents. Validate that Langfuse/Opik sink work is separate, read-only, and never in the benchmark trust path. It must depend on AEB-0 artifact and cannot affect metric_applyable, improvement_claim_allowed, policy_mutated, gate_advanced, roster selection, effective vote, or policy derivation. Check existing packet docs and repo trust-path surfaces. Return concise findings with exact file:line refs, required boundary tests, and packet changes.
```

Validation question:

Does the packet defer Langfuse/Opik into a separate read-only sink, require AEB-0 before ingest, and forbid sink output from satisfying trust-path evidence?
