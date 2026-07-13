# Implementation Plan: PILOT-001

## Execution Identity

- Planned run ID: `5a1f419c-8458-4d48-ab8c-8a288de1846d`
- Planned client token:
  `harness-v1-pilot-001-fc22851b-080f-46a2-9fa7-4a3cbe14139e`
- Current state: **BLOCKED / NOT RUN**

## Exact Prerequisites

1. PROGRAM-001, INTEGRITY-A, INTEGRITY-B, OBS-001, and REPLAY-001 pass their
   exit gates on a commit-pinned tree.
2. TRACE-001, LEDGER-001, GRADE-001, RUNTIME-001, TASK-001, and EXP-001 are
   integrated, not merely unit-tested.
3. TRACER-001 completes one generic and one Unity task through all arms and
   both runtimes, with ledger verification, trace closure, and an L2 cap.
4. Focused and cross-slice regression suites are green; no known append-only
   fixture conflict or provider-seam gap is waived silently.
5. Real Claude Code, Codex, official generic verifier, and Unity verifier
   availability is checked with pinned versions/hashes.
6. A named operator authorizes credentials, compute budget, storage, and
   expected wall time.
7. The unique pilot roster is frozen, hashed, and reserved away from
   confirmation/holdout/portability sets.
8. Before task one, freeze pilot task count, all A/B/C ceilings, assignment
   version and key custody, models, prompts, tools, CLIs, images, OS/arch,
   network/resource policy, verifier revisions, retry rules, common-infra
   rerun rule, and a non-optional stop rule.
9. Reviewer/approval gates required by the Harness v1 execution plan are
   available; no skill or reviewer receipt may be synthesized.

The numeric pilot task count and arm ceilings are intentionally unresolved
until the preregistration and budget are approved; they must not be invented in
advance or changed after outcomes are visible.

## Execution Sequence

1. Validate readiness and hash the frozen protocol.
2. Persist all task assignments before execution.
3. Run every unique task through isolated A/B/C arms under ITT.
4. Blind, verify, append grades, verify ledger chains, and close traces.
5. Freeze one task-level row per task and the raw artifact index.
6. Compute pilot estimates; never continue to chase discordance.
7. Derive and freeze the confirmation plan from the preregistered alternative.
8. Run ClaimGate and publish a descriptive, non-causal readout.

## Exact Required Outputs

- Signed/immutable pilot preregistration and manifest hash.
- Frozen pilot task roster, task-set hash, and confirmation-disjointness proof.
- Persisted assignment table and assignment-version/key-custody receipt.
- One row per unique task with A/B/C pass/fail, failure class, attempts, frozen
  result/grade hashes, cost, latency, runtime/model, and verifier revision.
- n11/n10/n01/n00 for B vs C and discordant-task count/rate with uncertainty.
- Verifier flake and common/treatment infrastructure-failure rates.
- Cost and latency summaries by arm plus complete task-level raw values.
- Missingness, retry, exclusion, and ITT accounting.
- Ledger verification and trace-closure reports.
- Frozen `PilotEstimate`.
- Frozen confirmation derivation containing alpha, power, preregistered
  alternative, required discordant pairs, observed pilot discordance, total
  unique confirmation tasks, total A/B/C runs, task-set exclusions, and hash.
- ClaimGate/readout explicitly refusing L3 and all causal/ROI language.
