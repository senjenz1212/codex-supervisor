# Harness v1 Verification — July 13-15, 2026

## Verdict

The branch contains a strong **hermetic L1 evidence kernel**. PROGRAM-001
through `TRACER-001-HERMETIC` are locally implemented and testable, but the
authoritative operational tracer is not complete. `TRACER-001-OPERATIONAL`,
PILOT-001, CONFIRM-001, OPT-001, DEPLOY-001, and SCALE-001 remain blocked.

“Ready to merge” for this branch therefore means the reachable hermetic kernel
and its fail-closed operational gates are reviewed and green. It does not mean
the Harness v1 program has demonstrated benchmark improvement or production
readiness.

The TRACE lifecycle receipt is explicitly labeled
`post_execution_stage_projection` with `pre_execution_attested=false`. It
proves immutable structural stage order, not wall-clock execution chronology.

This is **not** evidence that the harness improves coding outcomes. PILOT-001
has not run, so CONFIRM-001, OPT-001, DEPLOY-001, and SCALE-001 remain
ineligible.

## Reproducible Verification

The source, tests, proof registry, and release scripts verified below are
pinned by implementation commit:

```text
172e1ebf4680cd70ab6472dfd3dc9ba3e4a6182a
```

The follow-up receipt commit only records this immutable implementation
identity; it does not change the tested implementation.

The final ownership/recovery hardening tree is pinned by:

```text
26fbec7438a44011332856223ecb7c40ef54c01e
```

The following verification ran against the exact content committed at that
identity immediately before the commit was created; creating the commit did
not alter the tree:

```text
uv run --extra dev python -m pytest -q
2779 passed, 33 skipped in 2033.79s

uv run --extra dev python -m pytest --collect-only -q
2812 tests collected

make test-projection-registry
7 exact hermetic projection proofs passed
48 PostgreSQL conformance tests passed
6 registered PostgreSQL projection proofs were present in the exact manifest

uv run --extra dev python -m compileall -q supervisor mcp_tools scripts tests
git diff --check
both passed
```

The ordinary-suite PostgreSQL skips are not counted as release proof. The
separate projection-registry command applied the real Alembic migration chain
through `20260715_0002` against the pinned PostgreSQL 16 image and executed all
48 manifest entries without skips.

The final hardening adds four fail-closed boundaries: workflow runtime sessions
must inherit the authoritative parent task and exact binding event; evidence
recovery cannot publish behind a newer trusted checkpoint; historical
executions use persisted owner generations and heartbeats before side effects
or terminal publication; and dispatcher cancellation/recovery cannot trust an
unverified PID generation or a stale result file.

Two later fix commits landed after this receipt (review-finding fixes and
workflow-resubmission/containment/read-lock regression fixes). The full suite
was re-run on July 15, 2026 at the resulting branch head:

```text
fb93590142d53a8df3667e93d2de068440318c23
uv run pytest -q
2675 passed, 33 skipped in 1613.13s
uv run pytest --collect-only -q
2708 tests collected
make test-projection-registry
7 exact hermetic projection proofs passed
48 PostgreSQL conformance tests passed
```

The claim boundary below is unchanged by that re-run.

The final adversarial review-hardening tree is pinned by:

```text
3db6a8ed965ea0c4df2d58d8029c2f2863a9a5f5
```

The following verification ran on July 15, 2026 against the identical tree
immediately before that commit was created:

```text
uv run pytest -q
2835 passed, 33 skipped in 2105.87s

uv run pytest --collect-only -q
2868 tests collected in 0.93s

make test-projection-registry
7 exact hermetic projection proofs passed
48 PostgreSQL conformance tests passed
6 registered PostgreSQL projection proofs were present in the exact manifest

uv run python -m compileall -q supervisor mcp_tools scripts tests
git diff --check
both passed
```

This pass closes the review findings without raising the claim ceiling:

- decision verdict publication and outbox acknowledgement are one transaction,
  lease identity is fenced, and claim/settlement clocks are sampled only after
  SQLite has acquired the database write lock;
- legacy SQLite databases add `verdicts.decision_id` before creating its
  unique index, and oversized quality-projection evidence backfills require
  explicit offline maintenance;
- runtime cancellation is bounded and unconfirmed cleanup quarantines the
  workspace instead of racing teardown;
- provider adapters keep sync compatibility without blocking the event loop,
  historical redaction uses the exact pinned ruleset, evidence-committer
  connections close deterministically, and rollout drain locks do not leak;
  and
- malformed Unity verifier results fail closed while valid independent
  `passed` and normalized score fields remain intact.

These are integrity, replay, and process-assurance improvements. They are not
benchmark evidence that the harness improves coding outcomes. PILOT-001 and
every higher claim remain blocked by the prerequisites below.

Three later commits landed after this receipt (a retention/orphaned-process
review fix, a persisted-event authority test alignment, and documentation
sync). The full suite and the live PostgreSQL conformance lane were re-run on
July 15, 2026 at the resulting branch head:

```text
845074cfecda7a25181c99652b38cd4a2be6440f
uv run pytest -q
2835 passed, 33 skipped in 1666.13s
uv run pytest --collect-only -q
2868 tests collected
make test-postgres
48 passed
```

The claim boundary below is unchanged by that re-run.

The four post-audit runtime and trace hardening commits were then verified
together on July 15, 2026 at:

```text
4c3a88522bb84f9cc817690abef1696115be9110
uv run pytest -q
2853 passed, 33 skipped in 2102.15s (0:35:02)
make test-projection-registry
7 hermetic projection proofs passed in 4.10s
48 PostgreSQL conformance tests passed in 6.41s
6 exact PostgreSQL projection entries present
9 existing Alembic deprecation warnings
uv run python -m compileall -q supervisor tests
exit 0
git diff --check
exit 0
git diff --cached --check
exit 0
```

These are integration and consistency receipts, not operational benchmark,
causal-improvement, portability, ROI, or auto-improvement evidence.

One review commit then landed at the branch head:

```text
b594d022a87954085b41e59168a69c6c6f1647d9
```

It preserves the timeout failure taxonomy when containment finalization also
fails (the terminal event keeps `reason="timeout"` and records the
containment error separately), removes the unused
`verify_authoritative_event_chain` re-export from
`supervisor.evidence_ledger` (the persisted-checkpoint API in
`supervisor.ledger_checkpoints` remains the only verification entry point),
and documents the fail-closed agentic-worker cleanup scope. Focused re-run at
that head:

```text
uv run pytest -q tests/test_claude_sdk_runtime.py tests/test_agentic_workers.py
36 passed in 4.73s
```

No new full-suite receipt is recorded after
`4c3a88522bb84f9cc817690abef1696115be9110`; the claim boundary below is
unchanged.

Earlier full-suite checkpoint:

```text
uv run pytest -q
```

Observed result:

```text
2641 passed, 33 skipped in 1856.12s
```

Collection was checked separately:

```text
uv run pytest --collect-only -q
2674 tests collected in 0.74s
```

The 33 skips include the live PostgreSQL cases gated by
`CODEX_SUPERVISOR_POSTGRES_TEST_DSN`. Because Docker was available, the audit
did not accept those skips as release evidence. The release proof command:

```text
make test-projection-registry
```

starts an isolated `postgres:16-alpine` container on an ephemeral localhost
port when no DSN is supplied, applies the real migrations, executes the exact
PostgreSQL conformance manifest, and removes the container afterward. It also
runs every exact hermetic projection proof under sanitized pytest loading and
records per-test calls to the registered reducers and rebuilders. Observed
result:

```text
7 exact hermetic projection proofs passed in 3.87s
48 PostgreSQL conformance tests passed in 5.86s
6 registered PostgreSQL projection proofs were present in the exact manifest
7 existing Alembic deprecation warnings
```

The final changed-surface command covered every modified or newly added test
module for runtime, evidence commit, ledger, observability, PostgreSQL,
official SWE-bench authority, mergeability, task environment, trace graph, and
projection registry:

```text
uv run pytest -q tests/test_agent_runtime.py \
  tests/test_evidence_committer.py \
  tests/test_evidence_ledger_hardening.py \
  tests/test_experiment_kernel.py \
  tests/test_obs_001_observability.py \
  tests/test_postgres_ledger_lane.py \
  tests/test_swe_bench_official_oracle_authority.py \
  tests/test_swe_bench_pro_mergeability_bridge.py \
  tests/test_task_environment.py tests/test_trace_graph.py \
  tests/test_projection_registry.py
```

Observed result:

```text
618 passed, 32 skipped in 56.46s
```

The skipped PostgreSQL cases in that ordinary pytest command are covered by
the live 48-test conformance run above.

An earlier post-audit checkpoint used the following focused command for
production-trace authority, public export reconstruction, event-ledger
migrations, replay, historical evaluation, run registration, and
launch-receipt durability:

```text
uv run pytest -q tests/test_production_trace.py \
  tests/test_dual_agent_artifacts.py tests/test_state_event_ledger.py \
  tests/test_evidence_ledger_conformance.py \
  tests/test_evidence_ledger_hardening.py tests/test_postgres_ledger_lane.py \
  tests/test_ledger_checkpoint_lifecycle.py tests/test_replay_strict.py \
  tests/test_version_drift_replay.py tests/test_supervisor_turn_replay.py \
  tests/test_historical_evaluation.py tests/test_run_registry.py \
  tests/test_obs_001_observability.py
```

Observed historical checkpoint result (the 23 skips were the Postgres-lane
tests without a reachable PostgreSQL server):

```text
295 passed, 23 skipped in 12.19s
```

Earlier authority-regression checkpoints covered immutable
experiment-to-grade joins, completed evidence replay, the hermetic tracer,
canonical public exports, clean-room verification, production-trace
recording, and terminal experiment recovery:

```text
uv run pytest -q tests/test_evidence_committer.py tests/test_tracer_001_e2e.py
uv run pytest -q tests/test_dual_agent_artifacts.py
uv run pytest -q tests/test_codex_supervisor_mcp_stdio.py -k production_trace
uv run pytest -q tests/test_experiment_kernel.py -k 'terminal or grade or replay'
```

Observed historical checkpoint results:

```text
35 passed in 12.36s
68 passed in 8.01s
5 passed, 47 deselected in 0.60s
22 passed, 73 deselected in 1.54s
```

An earlier full-suite run found two stale launch-receipt fsync-probe
assertions after the receipt store moved to descriptor-relative `openat`
operations and eager creation of its anchored `pending`, `consumed`, and
`locks` namespaces. The probe previously reported relative names instead of
resolving them against `dir_fd`, and it omitted several real durable
transitions. The probe now resolves descriptor-relative paths and asserts
every directory-entry fsync. The two focused tests, the 24-test OBS-001 suite,
the 39-test run-registry suite, and the final full suite all pass.

Two later adversarial reviews found authority gaps after an earlier green
suite. First, evidence publication snapshotted the experiment store and
GradeBook without proving their terminal records joined. Second, a clean-room
package could be internally hash-consistent while receipt-supplied trace
semantics differed from the canonical gate event. RED probes reproduced both.
The final implementation rejects missing, mismatched, deleted, or recommitted
terminal authority on both initial commit and completed replay, and rejects
non-`dual_agent` or semantically substituted clean-room trace sources. The
independent re-probes confirmed both bypasses closed before the final suite.

Static checks:

```text
uv run python -m compileall -q supervisor mcp_tools scripts tests
git diff --check
```

Observed result: both passed.

## Operational Compatibility Receipts

- `docs/dual-agent/runtime-001-seams-20260711/compatibility-claude-code-20260713.json`
  records a completed Claude Code smoke with resolved-model, token, duration,
  and cost provenance.
- `docs/dual-agent/runtime-001-seams-20260711/compatibility-codex-20260713.json`
  records a completed Codex smoke with the same output hash and token
  provenance. That receipt does not contain resolved-model or cost provenance.
- `docs/dual-agent/task-001-plugins-20260711/compatibility-unity-6000.3.10f1-20260713.json`
  records one successful Unity 6000.3.10f1 EditMode verifier smoke.

These receipts establish narrow operational compatibility only. They are not
representative coding benchmarks.

## Evidence and Authority Boundaries

- Policy derivation requires an L3-or-higher ClaimGate receipt, an exact
  report hash recorded in the append-only run ledger, candidate artifact
  hashes bound by that report, and a ledger-recorded proposal.
- Approval reloads the recorded proposal instead of trusting caller-provided
  proposal bytes.
- A named human approval is still required before policy bytes can change.
- The daemon has no ambient policy-claim authority resolver. Without an
  explicitly supplied, validated authority it can run report-only experiments
  but cannot derive an applyable policy proposal.
- Evaluator determinism hashes a Supervisor-owned canonical output projection;
  an evaluator-provided `determinism_payload` cannot hide changed outcomes.
- Workspace evidence excludes runtime databases, caches, generated run state,
  and credentials while preserving explicitly pinned planning artifacts.
- Grade-backed trace closure validates immutable grade revisions through the
  GradeBook rather than accepting stale citations by shape alone.
- L3 assignment evidence must match preregistered assignment-version,
  experiment-spec, treatment, key-commitment, and task-to-stratum hashes;
  self-consistent post-hoc re-randomization is rejected.
- If terminal persistence fails after a pass is graded, a failed replacement,
  invalidation, or immutable emergency quarantine prevents the orphan pass
  from authorizing a decision.
- Each event-hash schema is bound to exactly one frozen redaction ruleset:
  the current `evidence-ledger-event/v3` to `supervisor-redaction-rules/v2`,
  and the legacy `evidence-ledger-event/v2` to rules v1. Verification infers
  the schema from the event hash and never tries every known redactor.
- A real oversized subprocess stream line fails immediately and the process
  tree is reaped instead of waiting for the runtime timeout.
- Production TRACE derives dynamic evidence, completion state, and its
  workspace storage root from the ledger-verified gate-result event. Caller
  result/payload objects cannot override or suppress that event, and replaying
  the same source event emits no duplicate trace-recorded event.
- A completed evidence-commit replay reloads the persisted `TraceGraphStore`
  and rejects any difference from the immutable request instead of validating
  the caller graph alone.
- Evidence-commit schema v2 requires the complete lifecycle projection and a
  directly signed artifact-manifest attestation on completed replay. Legacy
  v1 materializations cannot silently inherit those guarantees.
- Manifest publication requires an instance-scoped committer capability plus
  one immutable state-level idempotency claim per `(run, commit_id)`. Exact
  replay returns the original event; missing, forged, or conflicting authority
  claims fail closed, including across distinct evidence roots.
- TRACE lifecycle v2 binds exact ordered parent projections. Existing v1
  lifecycle stores remain readable under their older record-set semantics;
  new writes use v2 rather than silently strengthening v1.
- Signature trust boundaries require the verifier to return the literal
  boolean `True`; truthy strings and async verifier coroutines fail closed.
- Quality-trend projection rebuild requires a non-empty external exact-stream
  checkpoint inventory. PostgreSQL rebuild locks the projection, stream
  sequence, and event tables in writer order so a concurrent new stream cannot
  be erased and an existing-stream update cannot deadlock the rebuild.
- Projection proof receipts attribute registered implementation calls to exact
  pytest node IDs and projection IDs. An unrelated passing test cannot satisfy
  another projection's proof, and a PostgreSQL rebuilder requires an exact
  registered PostgreSQL proof.
- The SQLite backend-run replay guard validates its canonical table and trigger
  definitions on every open; a pre-seeded or substituted same-name schema
  fails closed. Grade evidence is recursively immutable, while `Grade.to_dict`
  returns detached serialization-safe containers.
- Every grade revision in one supersession lineage must bind the same terminal
  experiment/task/arm/state/hash identity, regardless of commit order.
- Every published grade history request pins exactly one immutable terminal
  commit per revision plus the full source-authority terminal commit referenced
  by its exact experiment terminal event. Initial publication and completed
  replay reject deletion, substitution, or delete-and-recommit even when the
  replacement preserves terminal semantics but changes commit identity/hash.
- Public dual-agent exports now carry a canonical full-run ledger prefix in
  `replay/evidence-ledger.jsonl`, including every event-chain field required by
  `verify_event_chain` and the exact captured ledger head and event-identity
  head.
- Exported production-trace receipts are complete only when the trace-recorded
  event, source gate-result event, receipt, and receipt evidence all bind the
  same canonical ledger event ID and hash. Intervening unrelated run events do
  not break clean-room reconstruction because the complete prefix is exported.
- Every accepted execution or outcome-review gate must have exactly one
  verified production-trace record. Export generation and clean-room
  verification both reject missing, duplicated, omitted, or reordered trace
  coverage.
- Clean-room verification derives the production-trace receipt ceiling from
  the persisted L1 grade and decision. Rewriting a receipt to claim L2-L6,
  even with a recomputed receipt hash and package root, fails closed.
- Public and clean-room production-trace verification require the canonical
  source to be a `dual_agent` gate-result event and independently rederive its
  planning, runtime, result, workspace, status, trace, grade, terminal-commit,
  and decision semantics. Recommitted receipt/trace/grade bytes cannot replace
  absent or different source-event semantics.
- Failed trace persistence remains blocking unless a later, independently
  verified record has the exact same task, gate, source event ID, and source
  event hash. A manifest cannot forge recovery by editing status fields.
- `replay/export-integrity.json` commits the canonical exported file tree and
  captured ledger head. `DualAgentArtifactExport.export_root_sha256` exposes
  the resulting root for an independent verifier to pin outside the package.
- This export verification is explicitly `structural_prefix_only`. The package
  root detects later substitution only when obtained from an independent
  channel; it is not a signed external ledger anchor and does not raise the
  L1/L2 claim ceiling.
- The durable SWE-bench backend-run guard prevents replay, but it is not yet a
  crash-recoverable grade journal. A crash after durable consumption can be
  retried only as a fresh nonce-bound backend execution; a stable reused
  backend run fails closed. Operational grading remains blocked until a
  durable verification-attempt key, nonce, canonical grade, and completion
  state can be recovered atomically.

Focused public-export verification:

```text
uv run pytest tests/test_dual_agent_artifacts.py -q \
  -k 'release_export_copies_reconstructable_production_trace_authority or
      release_export_rejects_tampered_production_trace_store or
      release_export_rejects_production_trace_source_substitution or
      clean_room_export_rejects_forged_ledger_and_package_root or
      clean_room_verifier_rejects_forged_trace_claim_ceiling or
      clean_room_verifier_requires_one_record_per_trace_ledger_event or
      release_export_requires_one_trace_per_accepted_runtime_gate or
      clean_room_verifier_rejects_explicitly_missing_required_trace or
      clean_room_verifier_rejects_forged_recovery_of_trace_failure'
```

Observed result:

```text
9 passed, 59 deselected
```

## Current Claim Ceiling

The hermetic tracer demonstrates cross-slice composition and keeps its claim
at L1. Its same-principal fixture verifier is not independent operational
evidence for L2, and it does not provide the randomized, blinded, powered
B-vs-C evidence required for L3.

Therefore the current program must not claim:

- causal coding-outcome improvement;
- portability across task/model strata;
- positive ROI;
- safe autonomous improvement; or
- production deployment readiness.

## Pilot Blockers

PILOT-001 remains blocked until the program has:

1. a frozen, disjoint real-task roster and preregistered stop rule;
2. pinned A/B/C compute ceilings and assignment manifest;
3. an official live SWE-bench verifier receipt for generic tasks;
4. an independently controlled verifier identity;
5. production signing keys and an external ledger anchor;
6. named runtime, credential, and budget authorization; and
7. a commit-pinned execution tree; and
8. a crash-recoverable verification-attempt journal integrated with
   experiment resume.

The pilot must estimate discordance, verifier flake, infrastructure failure,
latency, and cost. Confirmation sample size and ROI remain undefined until
those estimates exist.
