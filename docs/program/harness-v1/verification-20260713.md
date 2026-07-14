# Harness v1 Verification — July 13-14, 2026

## Verdict

The evidence kernel and its local integration surfaces are implementation
complete for PROGRAM-001 through TRACER-001. The repository is green under the
full local test suite.

This is **not** evidence that the harness improves coding outcomes. PILOT-001
has not run, so CONFIRM-001, OPT-001, DEPLOY-001, and SCALE-001 remain
ineligible.

## Reproducible Verification

Final full-suite command:

```text
uv run pytest -q
```

Observed result:

```text
2443 passed, 24 skipped in 1572.17s
```

The final post-audit focused command covered production-trace authority,
public export reconstruction, event-ledger migrations, replay, historical
evaluation, run registration, and launch-receipt durability:

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

Observed result (the 23 skips are the Postgres-lane tests, which skip
without a reachable Postgres server):

```text
295 passed, 23 skipped in 12.19s
```

The final authority-regression commands covered immutable experiment-to-grade
joins, completed evidence replay, the hermetic tracer, canonical public
exports, clean-room verification, production-trace recording, and terminal
experiment recovery:

```text
uv run pytest -q tests/test_evidence_committer.py tests/test_tracer_001_e2e.py
uv run pytest -q tests/test_dual_agent_artifacts.py
uv run pytest -q tests/test_codex_supervisor_mcp_stdio.py -k production_trace
uv run pytest -q tests/test_experiment_kernel.py -k 'terminal or grade or replay'
```

Observed results:

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
7. a commit-pinned execution tree.

The pilot must estimate discordance, verifier flake, infrastructure failure,
latency, and cost. Confirmation sample size and ROI remain undefined until
those estimates exist.
