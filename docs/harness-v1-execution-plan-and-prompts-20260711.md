# Harness v1 — Validated Execution Plan + Implementation Prompts

Verdict on the evidence-first-kernel review: **converged.** Validation ran all seven new repo claims (7/7 confirmed with quotes) and recomputed the 90%-power table (exact: 263/114/65/42 discordant pairs at 60/65/70/75% B-win rates). This review is the strongest of the three and corrects the earlier ones in three places I had endorsed — I concede all three:

1. **Don't add `invoke()` to `TargetAgentAdapter`.** Validated: the protocol has only `health/normalize_hook/execute_action/supports_feature` — no lifecycle. Mixing invocation into the observation boundary would couple the agent-visible workspace with the observation seam and break hidden-verification isolation. Build a *separate* `AgentRuntime`. (Reverses my review-2 verdict item.)
2. **Don't extend the mergeability report into the efficacy experiment.** Validated: its arms are accept/reject decisions over one shared fixed candidate pool (`mergeability_bench.py:1954-1973`, pool-hash equality enforced at `:4860`). A real trial generates *different patches per arm*, paired by task. New experiment required. (Sharpens my review-1 "add the off-arm" — the off-arm isn't enough; you need a genuinely separate experiment.)
3. **The three-arm design (A/B/C) beats two arms.** A = production baseline, B = supervisor, C = compute-matched direct (same resource ceilings as B, no supervisor structure). **Primary comparison is B vs C** — this isolates *harness organization* from *merely spending more compute/retries*, which B-vs-A confounds. This is the single sharpest idea in any of the three reviews.

Plus one genuine new security finding, validated: **symlink escape** on the overlay write path (`policy_evolution.py:273-276` writes through symlinks; no `realpath`/`O_NOFOLLOW` guard). And the ledger is confirmed append-*oriented*, not tamper-evident (no hash chain; a test at `test_state_event_ledger.py:142-151` literally deletes events).

## Improvements I made to the review's execution plan

- **Bundled 17 slices → 13** by merging same-surface work: the four Phase-0 integrity items become two slices (completion/evidence integrity incl. symlink + audit-append; and cancellation integrity). Fewer gate cycles = less calendar, which is the real cost here.
- **Folded `ClaimGate` into PROGRAM-001.** Validation (review-1) already showed `improvement_claim_allowed` is hardcoded-false everywhere; making it *derived* rather than manually settable is the enforcement that makes the honesty ladder real, so it belongs with the ladder.
- **Kept the durable-engine migration deferred** (SCALE-001 last, gated on the readout) — consistent with all three verdicts and the just-hardened dispatcher.
- **Marked slices 13–16 as pilot-gated:** their exact parameters (sample size, arm ceilings) *cannot* be written now — they derive from PILOT-001's discordance estimate. Writing fixed numbers would be the fake-precision failure all three reviews warn about. Their prompts below are structurally complete with parameters marked `«from pilot»`.

## Slice sequence (13, ordered)

| # | Slice | Gate to start |
|---|---|---|
| 1 | PROGRAM-001 — charter, claim ladder, ClaimGate, forbidden claims | now |
| 2 | INTEGRITY-A — terminal CAS + append-only audit + symlink guard | now (parallel) |
| 3 | INTEGRITY-B — process-group cancel + stale-worker kill | now (parallel) |
| 4 | OBS-001 — watcher normalization, run registration, ID joins, drift decouple | now |
| 5 | REPLAY-001 — strict schema, recorded-checkout replay | now |
| 6 | TRACE-001 — namespaced versioned trace graph + closure gate | after 1 |
| 7 | LEDGER-001 — hash chain, append-only enforcement, checkpoints | after 2 |
| 8 | GRADE-001 — immutable grade revisions + invalidation | after 7 |
| 9 | RUNTIME-001 — AgentRuntime + ModelClient; Claude Code + Codex | after 3 |
| 10 | TASK-001 — TaskEnvironmentAdapter + VerifierAdapter; generic + Unity | after 9 |
| 11 | EXP-001 — A/B/C protocol, assignment, isolation, task-level outcomes, blinding | after 8,10 |
| 12 | PILOT-001 — disjoint operational pilot; estimate discordance/flake/cost | after 11 |
| 13 | CONFIRM-001 → OPT-001 → DEPLOY-001 → SCALE-001 | pilot-gated, sequential |

---

## Common submit parameters (referenced by every prompt below)

```
cwd=/Users/sam.zhang/Documents/codex-supervisor
quality=best · execution_layer_mode=lead_direct · agentic_lead_policy=off
require_skill_receipts=true · cursor_review=true · cursor_review_profile=rigorous
cursor_review_gates=["tdd_review","implementation_plan","outcome_review"]
reviewer_output_mode=cursor_sdk · reviewer_unavailable_policy=block
planning_artifacts=<five Step-1 docs w/ sha256> · tool_receipts=<five stage receipts>
```
Every slice: adopt the supervisor rigorous flow, PRD→TDD first, five skill receipts (to_prd, prd_grill, to_issues, tdd, tdd_grill), submit via `codex-supervisor-axi submit` (MCP fallback with same client_token), poll to terminal, never restart from zero, commit only on outcome_review accept. Pre-flight STOP on any uncommitted source change except docs/.scratch dirt. **Every slice's own tdd.md must pass the TDD-coverage floor it may introduce.**

---

## Slice 1 — PROGRAM-001

```text
TASK — Establish the harness-v1 program pack, claim ladder, and derived ClaimGate.
Adopt the supervisor rigorous flow. PRD→TDD first. Claude Code gate review AND Cursor SDK rigorous review.
GOAL
A machine-checkable honesty hierarchy where no report can assert a claim level it lacks evidence for — enforced in code, not by convention.
SCOPE
1. Create docs/program/harness-v1/{charter.md, claim-ladder.yaml, claims.yaml, forbidden-claims.md, architecture.md, roadmap.md, decisions/, experiments/, reports/} and a legacy-map.yaml mapping existing PRDs/ADRs/benchmark artifacts into the graph (reference, do not rewrite history).
2. claim-ladder.yaml encodes L0 Integrity → L1 Process → L2 Outcome → L3 Causal → L4 Portable → L5 ROI → L6 Auto-improvement, each with its required-evidence predicate.
3. Implement supervisor/claim_gate.py: ClaimGate.max_claim_level(evidence_bundle) -> level, derived purely from evidence present (pins+hashes+artifacts→L0; traceable detector→L1; independent hidden verifier→L2; randomized powered B-vs-C→L3; strata replication→L4; +operating cost→L5; frozen-control+sealed-holdout+canary→L6). improvement_claim_allowed and powered_improvement_claim_allowed become DERIVED outputs of ClaimGate — report producers may no longer set them. Grep the repo for every literal assignment of these flags and route them through ClaimGate; a manual set is a hard error.
4. forbidden-claims.md lists claims no current evidence supports (e.g. "supervisor improves outcomes") and a validator that fails any report asserting them without the matching ClaimGate level.
TESTS
- A fixture-replay bundle resolves to max L1; asserting L3 fails closed.
- A bundle with an independent hidden verifier resolves to L2; without one, L2 is refused.
- Any code path that sets improvement_claim_allowed=true without a ClaimGate L3 derivation fails a test.
- A report asserting a forbidden claim without evidence is rejected.
STEP 1 — PRD to TDD; create docs/dual-agent/program-001-claim-kernel-20260711/{prd,grill-findings,issues,tdd,grill-findings-tdd}.md; five receipts.
STEP 2 — codex-supervisor-axi submit [common params] task_id=program-001-claim-kernel-20260711 run_id=fc7357a5-2bfd-466d-8c64-d456c559c42c intent="Machine-checkable claim ladder with a derived ClaimGate replacing manual improvement-claim flags." client_token=harness-v1-program-001-178ed8fe-9064-4a3a-b981-1b900e8b4ca2
DONE — tests green; reviewer accept; commit "Add harness-v1 program pack and derived ClaimGate"; report the ClaimGate refusing L3 on a fixture bundle.
```

## Slice 2 — INTEGRITY-A (evidence-write integrity)

```text
TASK — Terminal-completion compare-and-set, append-only audit, and symlink-safe policy writes.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
Stop three confirmed evidence-corruption paths: terminal overwrite, in-place audit mutation, and symlink-escaping overlay writes.
SCOPE
1. Terminal CAS in complete_dual_agent_workflow_job (state.py:2014-2090): read existing terminal_outcome_json; identical second completion → idempotent no-op; different → write a terminal_discrepancy event, preserve BOTH, never overwrite the original; fail closed. Mirror the existing guard at state.py:1985 (AND terminal_outcome_json IS NULL) + rowcount branch.
2. Append-only audit: replace the in-place UPDATE in update_quality_trend_audit (state.py:924-968) with an INSERT into a new immutable quality_trend_audits table keyed (run_id, gate, computed_at); keep supervisor_quality_trends as a rebuildable projection over it.
3. Symlink guard: before any policy overlay/backup write (policy_evolution.py:273-276) and in normalise_overlay_target (policy_overlay.py:97-121), assert os.path.realpath(target) and realpath(backup) resolve inside realpath(repo_root) AND no path component is a symlink; prefer os.open(..., O_NOFOLLOW). Reject escape with PolicyOverlayError.
TESTS
- Duplicate identical completion → one row, no second terminal event; different completion → discrepancy event, original intact.
- Two audits on the same (run_id,gate) produce two immutable rows; the projection reflects the latest without losing the prior.
- overlay path (or a parent dir) as a symlink pointing outside repo → write rejected; legitimate in-repo write still succeeds.
STEP 1 — docs/dual-agent/integrity-a-evidence-writes-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=integrity-a-evidence-writes-20260711 run_id=c7fa0dbd-1396-4e9c-a266-6c0b5e5e6f59 intent="Terminal CAS, append-only audit revisions, and symlink-safe overlay writes." client_token=harness-v1-integrity-a-c7a1bde8-3198-495c-a3be-d909180d1c79
DONE — commit "Harden evidence writes: terminal CAS, append-only audit, symlink-safe overlay"; report the discrepancy event and a rejected symlink write.
```

## Slice 3 — INTEGRITY-B (cancellation integrity)

```text
TASK — Process-group cancellation and stale-worker termination.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
A cancelled or stale-leased worker leaves no surviving descendant agent CLI.
SCOPE
1. _terminate_process (workflow_job_dispatcher.py:366-385): since workers spawn with start_new_session=True, signal the whole group — os.killpg(os.getpgid(pid), SIGTERM) → bounded wait → SIGKILL → assert no descendants remain.
2. Stale-lease reaper (_fail_spawned, :273-288): currently marks the job failed WITHOUT killing the process — add the same group-kill before marking failed.
3. Verify no orphaned claude/codex child survives either path.
TESTS
- Spawn a worker that forks a long-lived child; cancel → both die (assert getpgid group empty).
- Stale-lease path kills the process group before marking failed.
- Idempotent: killing an already-dead group is a no-op, not an error.
STEP 1 — docs/dual-agent/integrity-b-cancellation-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=integrity-b-cancellation-20260711 run_id=7443c73e-a556-4c45-b44c-b665b958f33f intent="Kill full process group on cancel and stale lease; no surviving descendants." client_token=harness-v1-integrity-b-88f99ff7-a328-449f-b50d-a6e1da389690
DONE — commit "Kill process groups on cancel and stale lease"; report a cancel-kills-child proof.
```

## Slice 4 — OBS-001 (observability repair)

```text
TASK — Repair rollout event normalization, run registration, workflow/run joins, and decouple semantic drift.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
Live drift monitoring sees real events, every run joins to its task/workflow, and semantic checks run without requiring path violations.
SCOPE
1. _extract_kind (rollout_watcher.py:304-308): descend into nested payload.type (real Codex rollouts wrap the type under event_msg/response_item); map raw Claude/Codex/OpenCode events to a normalized terminal taxonomy (run.started/turn.started/tool.started/tool.completed/agent.message/turn.completed/turn.failed/run.completed/run.failed/run.cancelled). Terminal handling (:240-248) matches the normalized names.
2. Write run-registry records at workflow submission (stdio submit path): store both workflow run_id AND target-agent session_id; make active_runs()/drift union workflow runs OR co-register on a shared join key.
3. Decouple L2/L3 drift (drift_detector.py:190-199) from the l1_scope_violation_threshold gate: any high-confidence signal (goal-similarity, plan-progress, loop/repetition, tool-error, time-without-progress) may open adjudication; path violation is no longer a prerequisite.
4. Add REAL captured rollout fixtures (nested schema), not flattened synthetic ones.
TESTS
- A real nested Claude and Codex rollout each reach the correct terminal state; no run stuck "running" after a terminal event.
- Every ingested event joins to one task, one run, one workflow.
- Goal abandonment inside allowed files (zero path violations) triggers semantic evaluation.
STEP 1 — docs/dual-agent/obs-001-observability-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=obs-001-observability-20260711 run_id=d30b3005-83f8-4bba-9479-e8706a98ccce intent="Nested event normalization, run registration + joins, and path-independent semantic drift." client_token=harness-v1-obs-001-b87dd5e8-ff79-4383-8e98-25f9b1b82fe1
DONE — commit "Repair rollout normalization, run registration, and semantic-drift decoupling"; report the submission→registration→normalized-event→terminal→evaluation trace.
```

## Slice 5 — REPLAY-001

```text
TASK — Strict replay compatibility and recorded-checkout re-evaluation.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
Missing/unknown schema fails closed; historical audits verify against the run's recorded commit, never the live checkout.
SCOPE
1. replay_versions.py:57-68: missing mandatory schema → "incompatible"; unknown schema → "incompatible" (today missing schemas silently return "compatible").
2. Historical re-evaluation (quality_trends.py:156-206): check out the run's recorded commit / immutable snapshot before re-verifying, never Path.cwd()/live worktree. Distinguish rerun (execute again), regrade (same captured result, new verifier), replay (recompute deterministic logic from frozen inputs) as separate operations.
3. Every model alias resolved and recorded; every prompt/tool-contract/container/CLI/evaluator hashed in the run manifest.
TESTS
- A replay artifact with no schema declaration → incompatible.
- A P11 audit rerun after the working tree changes yields the SAME historical result (recorded-commit isolation).
- A resolved-model field is present for a lane recorded as "default".
STEP 1 — docs/dual-agent/replay-001-strict-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=replay-001-strict-20260711 run_id=279d8771-0f43-43b4-b8f9-3803cc53c432 intent="Strict replay schemas and recorded-checkout audits; resolved/hashed run manifests." client_token=harness-v1-replay-001-3e60b2a7-816c-4d2c-94bf-ddcd2482e8f3
DONE — commit "Strict replay compatibility and recorded-checkout re-evaluation"; report an incompatible-on-missing-schema case and a history-immutable audit.
```

## Slice 6 — TRACE-001

```text
TASK — Namespaced versioned trace graph with a closure gate.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
The Objective→…→Promotion path is a typed, queryable graph, and any slice with a broken chain blocks.
SCOPE
1. Node types (OBJ/CLAIM/REQ/ADR/ISSUE/TEST/TASK/EXP/ASN/RUN/ART/GRADE/ANL/DEC/POL/DEP) and edges (implements/tests/supports/contradicts/derived_from/assigned_by/evaluates/supersedes/invalidates/promotes/rolls_back). Adopt W3C PROV entity/activity/agent concepts rather than inventing vocabulary.
2. Three-part identity: logical ID (CLAIM-HARNESS-001), revision hash (sha256), instance ID (UUIDv7). Namespace existing local IDs so REQ-agent-supervisor:P1 ≠ REQ-powered-benchmark:P1.
3. Closure gate (extends planning_validator.py:343-423): block on a requirement without a test, a test without runtime evidence, evidence without a pinned run, a score without a verifier, a decision without an analysis, a promotion without a decision, or an uncovered node without a signed, expiring waiver.
TESTS
- From any promotion node, one query reaches objective→requirement→tests→assignment→run→evidence→grade→decision.
- A requirement with no test → gate blocks; adding a signed expiring waiver → passes; expired waiver → blocks again.
- Two unrelated P1 promises in different namespaces do not collide.
STEP 1 — docs/dual-agent/trace-001-graph-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=trace-001-graph-20260711 run_id=56b89aa5-c67e-4929-bf4c-88eb7bc3f8ed intent="Typed namespaced trace graph with an end-to-end closure gate and expiring waivers." client_token=harness-v1-trace-001-26e0d12d-3cc0-4c7c-a0fc-7418adc6799d
DONE — commit "Add versioned trace graph and closure gate"; report a blocked broken-chain and a passing closed one.
```

## Slice 7 — LEDGER-001

```text
TASK — Tamper-evident append-only evidence ledger.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
Deleting, changing, reordering, or truncating an event is detectable; every projection rebuilds from the ledger.
SCOPE
1. Add previous_event_hash, event_hash, canonical_payload_hash, artifact_manifest_hash to the events schema (state.py:87-96) and chain them in write_event (:566-604).
2. Reject direct UPDATE/DELETE on events in BOTH SQLite (triggers) and PostgreSQL; remove/replace the deletion-tolerating test (test_state_event_ledger.py:142-151) with one asserting deletion is rejected.
3. Content-addressed artifact store + signed manifests; periodically sign the stream head and anchor it outside the DB. Adopt in-toto/SLSA-style attestation shape for artifact/execution provenance.
4. SQLite/PostgreSQL conformance suite; deterministic projection rebuild (supervisor_quality_trends and others become pure projections).
TESTS
- Tamper (edit/delete/reorder/truncate) any event → chain verification fails.
- UPDATE/DELETE on events rejected in both backends.
- Every projection rebuilt byte-identical from the ledger.
STEP 1 — docs/dual-agent/ledger-001-tamper-evident-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=ledger-001-tamper-evident-20260711 run_id=94e8b5dc-1c06-42a4-8321-0be77a0a10e5 intent="Hash-chained append-only ledger with UPDATE/DELETE rejection and rebuildable projections." client_token=harness-v1-ledger-001-a796a493-3bfd-4752-8cb8-6d13ea182094
DONE — commit "Add tamper-evident append-only evidence ledger"; report a detected tamper and a rebuilt projection.
```

## Slice 8 — GRADE-001

```text
TASK — Immutable grade revisions with invalidation lineage.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
A grade is never overwritten; re-grading appends a new revision that supersedes the prior.
SCOPE
1. GradeRevision record: immutable RunEnvelope hash, verifier ID/version/config, score, evidence, failure+flake classification, supersedes_grade_id.
2. Regrade appends (new verifier, same captured result); the superseded grade stays queryable; the trace graph edge is supersedes/invalidates.
3. Wire into the closure gate: a score with no verifier, or a decision citing a superseded grade without acknowledging it, blocks.
TESTS
- Re-grading a run produces a second immutable revision; the first is retained and linked.
- A decision citing an invalidated grade blocks.
STEP 1 — docs/dual-agent/grade-001-revisions-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=grade-001-revisions-20260711 run_id=2564e0a7-bff6-4c7f-9c55-3e1e96337276 intent="Append-only grade revisions with supersession lineage." client_token=harness-v1-grade-001-41d40c66-5a00-434a-96cc-4eb4856d1256
DONE — commit "Add immutable grade revisions"; report a supersession chain.
```

## Slice 9 — RUNTIME-001

```text
TASK — Provider-neutral AgentRuntime and ModelClient; Claude Code and Codex runtimes.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
Every model call — lead, judges, drift adjudication, planning, recovery — passes through a provider-neutral seam; no core module imports a provider SDK.
SCOPE
1. New AgentRuntime protocol (start/resume/cancel/stream/collect) SEPARATE from TargetAgentAdapter (which stays observation/steering — do NOT extend it). New ModelClient (complete/structured_complete). Keep TaskEnvironmentAdapter/VerifierAdapter/ReviewerAdapter as distinct seams (build the latter two in TASK-001).
2. ClaudeCodeRuntime and CodexRuntime implementations; capability discovery (tool restrictions, resume, subagents, image, cost reporting, cancellation, streaming).
3. Route AgentInvoker (agent_invoker.py:34-92,142-145) and the other hard-coded provider sites (telegram_supervisor, hook_critic, agentic_executor claude argv, cursor reviewer path) through the seam. Provider contract tests: same task+evidence schema across runtimes.
TESTS
- The same task definition runs on ClaudeCodeRuntime and CodexRuntime with identical run/result schemas.
- No provider SDK import remains in core experiment modules (grep-enforced test).
- A judge/adjudication call goes through ModelClient, not a direct SDK client.
STEP 1 — docs/dual-agent/runtime-001-seams-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=runtime-001-seams-20260711 run_id=6e077d7a-4434-4f4a-9914-398240f0adea intent="AgentRuntime + ModelClient seams; Claude Code and Codex runtimes; no provider SDK in core." client_token=harness-v1-runtime-001-10c91222-23c9-4f00-a9f8-f4d6a8dbf3b3
DONE — commit "Add provider-neutral AgentRuntime and ModelClient seams"; report the same task on both runtimes.
```

## Slice 10 — TASK-001

```text
TASK — Task-environment and verifier seams; generic + Unity task plugins.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
Tasks and hidden verifiers are pluggable and isolated; the agent-visible workspace never sees hidden tests before result freeze.
SCOPE
1. TaskEnvironmentAdapter (materialize/reset/collect_patch/teardown) and VerifierAdapter (verify(frozen_result)->Grade) as separate interfaces (hidden verification isolated from agent workspace).
2. GenericRepositoryTask + UnityRepositoryTask; SWEbenchVerifier (wraps the official unforked harness) + UnityTestFrameworkVerifier. A task-family plugin may bundle env+verifier but the interfaces stay separate.
3. TaskSpec pins: repo+revision, task/dataset/split hash, public problem statement, image digest, arch/OS, network policy, resource limits, verifier ID+hash.
TESTS
- One non-Unity and one Unity task both materialize, run, and grade with identical schemas.
- Hidden tests are inaccessible from the agent workspace before result freeze (leak-check).
- SWEbenchVerifier scoring semantics are the official harness's, unmodified.
STEP 1 — docs/dual-agent/task-001-plugins-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=task-001-plugins-20260711 run_id=17429aab-9bd3-42f1-b650-def4374bfbe5 intent="Task-environment and verifier seams with generic + Unity plugins and hidden-test isolation." client_token=harness-v1-task-001-d656b787-3178-436d-9d82-bc313200c970
DONE — commit "Add task-environment and verifier seams"; report a leak-check pass and an official-harness grade.
```

## Slice 11 — EXP-001

```text
TASK — Task-efficacy A/B/C experiment kernel with blinding and task-level outcomes.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
A preregistered A/B/C experiment where the unit is one unique task, arms are isolated, grading is blinded, and the primary comparison is B-vs-C.
SCOPE
1. Arms: A=production baseline, B=supervisor, C=compute-matched direct (same ex-ante ceilings as B, no supervisor structure). Primary=B vs C; secondary B vs A, C vs A.
2. Experimental unit = one unique underlying task; all retries/reviewers/subagents inside an arm collapse to ONE final task outcome (never count attempts/patches as observations).
3. Assignment: generate once, persist before execution, randomize the six A/B/C orders, block by repo+task-class+model, clean worktree/container/session per arm, no shared lessons/caches/memory, retries stay in-arm. Sticky key HMAC(experiment_id||stable_task_id||assignment_version); Supervisor owns and records assignment (OpenFeature may deliver, not decide).
4. Blinding: run arm → freeze+hash result → strip arm identity → official verifier → join grade to arm afterward. Reviewer models get task+diff+evidence WITHOUT the lead's claimed outcome; outcome-aware adjudicator sees both only after primary reviews.
5. Failure handling = intention-to-treat: crash/timeout/empty-patch/patch-doesn't-apply/over-budget/treatment-specific-provider-failure all → failure. A common pre-treatment infra failure may rerun the whole A/B/C block once, never silently.
TESTS
- Assignment is deterministic from the HMAC and persisted before any run; a retry lands in its original arm.
- The verifier receives arm-stripped frozen results; arm is joined only post-grade.
- A crashed arm scores as failure (ITT), not excluded.
- Reviewer packets contain no lead outcome; adjudicator packet does, delivered after.
STEP 1 — docs/dual-agent/exp-001-abc-kernel-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=exp-001-abc-kernel-20260711 run_id=bb755c57-c9db-4b24-97b2-8aecbc057a05 intent="A/B/C task-efficacy kernel: task-level unit, isolated arms, blinded grading, B-vs-C primary." client_token=harness-v1-exp-001-7233bc3b-4a60-474c-bfc1-20026f32bd62
DONE — commit "Add task-efficacy A/B/C experiment kernel"; report a deterministic assignment set and a blinded grade join.
```

## Slice 12 — TRACER-001 (end-to-end thread)

```text
TASK — One generic and one Unity A/B/C task end-to-end through the full kernel.
Adopt the supervisor rigorous flow. PRD→TDD first. Both reviewers.
GOAL
Prove the whole spine on two real tasks before spending pilot budget: submission→registration→normalized events→runtime→frozen envelope→blinded verifier→immutable grade→trace closure, with the ClaimGate capping the claim at L2 (no causal claim from two tasks).
SCOPE
1. Run one GenericRepositoryTask and one UnityRepositoryTask through all three arms on both ClaudeCodeRuntime and CodexRuntime.
2. Assert identical run/result schemas, hidden-test isolation, blinded grading, tamper-evident ledger entries, and a closed trace graph from objective to grade.
3. ClaimGate must refuse anything above L2 for this run.
TESTS
- Both tasks reach terminal on both runtimes; every event joins; the trace closes; the ledger verifies; ClaimGate caps at L2.
STEP 1 — docs/dual-agent/tracer-001-e2e-20260711/{...}; five receipts.
STEP 2 — axi submit [common] task_id=tracer-001-e2e-20260711 run_id=d4d9c4bd-36d7-4ad0-92f5-fbe21365b701 intent="End-to-end A/B/C thread on one generic and one Unity task; ClaimGate capped at L2." client_token=harness-v1-tracer-001-0e8f285d-c042-4355-933e-e318704f8754
DONE — commit "Prove end-to-end A/B/C thread on two tasks"; report the closed trace and the L2 cap.
```

## Slices 13–16 — PILOT-gated (parameters derive from PILOT-001; do NOT fix numbers earlier)

**PILOT-001** (run_id `5a1f419c-8458-4d48-ab8c-8a288de1846d`, client_token `harness-v1-pilot-001-fc22851b-080f-46a2-9fa7-4a3cbe14139e`): run the A/B/C kernel on a DISJOINT task set to estimate discordance rate, verifier flake rate, infra-failure rate, cost, latency. Forbidden: using pilot tasks in confirmation, tuning on confirmation tasks, using an optimistic pilot effect for power, or running until enough discordant pairs appear. Output: the estimates + a frozen sample-size derivation.

**CONFIRM-001** (run_id `5f2ec18c-12d6-42c5-8793-9c9738a72b3c`, client_token `harness-v1-confirm-001-3b168d27-a42b-41ec-8a75-3cc942672ce0`): freeze total task count BEFORE confirmation using the pilot's discordance estimate and the validated 90%-power table (**discordant pairs needed: 263 @60% B-win, 114 @65%, 65 @70%, 42 @75%** — recomputed exact-binomial, two-sided α=0.05). e.g. 20% discordance + 65% alternative ⇒ 114/0.20 ≈ 570 unique tasks ≈ 1,710 A/B/C runs. Report n11/n10/n01/n00, exact McNemar, Newcombe paired risk-difference CI (never armwise Wilson), repo-block bootstrap, leave-one-repo-out, model/task strata (no pooling that hides regressions), best/worst-case missingness, every task row. ClaimGate rises to L3 only if the paired test clears; L4 only across ≥3 pinned model families incl. one GEPA never saw.

**OPT-001** (run_id `3b168d27-a42b-41ec-8a75-3cc942672ce0`→use `c079de91-8c5d-4a4b-ae39-846e1835038b`, client_token `harness-v1-opt-001-a88db909-bf49-451b-8a50-ee876672a90a`): **only if CONFIRM-001 shows B beats C.** GEPA as candidate generator against ONE task-class/gate overlay slot (never a global prompt — Eevee arXiv:2606.11182 shows −15.4 retention under mixture). Control arms: frozen policy, no-op optimizer, human-authored, MIPROv2. Five data partitions (discovery/tuning/selection sealed-holdout/retention/external-portability); optimizer sees 1–3 only. Lessons namespaced by experiment||arm||policy-version||runtime||provider||task-family. GEPA never assigns arms, sees hidden labels, sets claims, or promotes.

**DEPLOY-001** (run_id `9d6a5885-c008-48fe-a3e4-92d15d16c448`, client_token `harness-v1-deploy-001-7a3d09a1-cb3a-470a-a1c8-447fc0522880`): candidate lifecycle generated→screened→tuned→selected→frozen→sealed-confirmed→retention-passed→approved→**shadow**→canary→promoted→rolled-back. Change approval (policy_evolution.py:260-323) to authorize SHADOW, not live activation. OpenFeature sticky canary assignment; Argo Rollouts for the service (not for deciding prompt quality); automatic rollback on guardrail failure; named-human promotion.

**SCALE-001** (run_id `df1778c5-e098-4c9f-ac58-9a99741e9ced`, client_token `harness-v1-scale-001-aa6d72cc-8f25-4b9a...`→regenerate): **only after outcome value is proven.** Durable-engine bakeoff (Temporal MIT vs Restate BSL-1.1-needs-legal) against the 18-test crash/approval/cancellation/versioning matrix; choose lowest operational burden among engines passing every correctness gate, not fewest demo lines. Replace only workflow_job_dispatcher + lease/recovery in state.py; never the ledger/schemas/gates/adapters/receipts/promotion logic.

---

## What to keep / change (unchanged from the review, validated)

Keep: PRD Promise Contracts + public-boundary testing, immutable run-snapshot intent, runtime-native receipt provenance, official oracle integration, report-only authority flags, overlay whitelist, named-human approval, hash-pinned rollback, ADR-0004 durable-engine deferral. Change: free-form traceability → typed graph; append-oriented → tamper-evident ledger; mutable audits → immutable revisions; mergeability stats → separate task-efficacy experiment; adapter overloading → separate runtime/observation/task/verifier seams; direct approval-to-live → shadow/canary; global lessons → arm/policy-isolated; manual claim flags → derived ClaimGate.

## ROI (operating cost only; dev cost ignored per operator)

`incremental_successes = N × (success_B − success_C)`; `cost_per_incremental_success = (cost_B − cost_C)/(successes_B − successes_C)`; `δ_break_even = (incremental cost + latency cost + expected risk cost)/value of one verified success`, derived BEFORE the pilot, provisional +5pp only if no business number exists. Five distinct outcomes must stay separable: B>C ROI+ (works, worthwhile); B>C ROI− (works, uneconomic); B>A but not C (compute helped, structure unproven); no difference (no claim); mixed strata (claim restricted to winning strata). The scarce resource is trustworthy evidence, not code.

## Immediate action

Slices 1–5 are runnable now and 2/3/4/5 are parallel-safe. Start PROGRAM-001 + INTEGRITY-A + INTEGRITY-B together; do not start any efficacy run until Phase 0 (1–5) passes its exit gates. Commit this doc and the two prior verdict docs — they are the decisions-so-far index.

## Sources
Validation agents in-session (2026-07-11, 7/7 confirmed). Power tables recomputed exact-binomial in-session. Prior verdicts: docs/external-review-verdict-20260711.md, docs/external-review-2-verdict-20260711.md. [W3C PROV](https://www.w3.org/TR/prov-overview/) · [in-toto/SLSA](https://slsa.dev/) · [GEPA](https://arxiv.org/abs/2507.19457) · [Eevee](https://arxiv.org/abs/2606.11182) · [SWE-bench harness](https://www.swebench.com/SWE-bench/reference/harness/) · [Harbor](https://www.harborframework.com/) · [Inspect AI](https://inspect.aisi.org.uk/) · [OpenFeature](https://openfeature.dev/) · [Argo Rollouts](https://argo-rollouts.readthedocs.io/) · [judge bias, Zheng 2023](https://arxiv.org/abs/2306.05685).
