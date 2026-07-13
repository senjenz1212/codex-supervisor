# Harness v1 Verification — July 13, 2026

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
2175 passed, 22 skipped in 1364.57s
```

Focused changed-surface command covered policy authority, evaluator
determinism, evidence capture, manifests, trace closure, MCP/AXI boundaries,
the experiment kernel, the hermetic tracer, and request-path isolation.

Observed result:

```text
525 passed in 404.83s
```

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

## Current Claim Ceiling

The hermetic tracer demonstrates cross-slice composition and keeps its claim
at L2. It does not provide the randomized, blinded, powered B-vs-C evidence
required for L3.

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
