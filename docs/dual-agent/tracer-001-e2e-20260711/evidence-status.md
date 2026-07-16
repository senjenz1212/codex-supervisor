# Evidence Status: TRACER-001-HERMETIC

## Status

**GREEN for hermetic composition only. Operational/external efficacy is NOT
RUN and is not claimed. The achieved and asserted ClaimGate level is L1.**

The original operational requirement is tracked separately as
`TRACER-001-OPERATIONAL` and remains blocked.

The persisted TRACE lifecycle is a post-execution projection of one already
completed graph. Its hash chain proves immutable stage partitioning and order;
it does **not** attest that planning was persisted before runtime execution.
New lifecycle revisions use schema v2 for exact ordered-parent semantics;
legacy v1 stores remain readable under their original record-set semantics.

## Dedicated Hermetic Proof

Command:

```text
uv run pytest -q tests/test_tracer_001_e2e.py
```

Result on 2026-07-13:

```text
3 passed in 5.05s
```

The focused tests prove that the existing public seams compose locally:

- one pinned local generic repository and one pinned local Unity-shaped
  repository;
- all A/B/C arms through both `ClaudeCodeRuntime` and `CodexRuntime`;
- exactly 12 arm executions using deterministic in-process fake transports,
  zero provider calls, zero reported cost, and no network use;
- 12 unique fresh Git workspaces, absent before each marker write and removed
  before hidden verification;
- attempted hidden-fixture reads denied by the fake transport's workspace
  guard;
- treatment metadata present in each original frozen result, recursively
  removed before the verifier receives the blinded result;
- one initial and one superseding immutable `GradeBook` revision per
  execution, preserving 24 revisions and 12 supersession invalidations;
- one immutable terminal commit for each of those 24 revisions, including
  every current regrade, bound to the exact persisted experiment, task, arm,
  terminal state, and terminal-state hash;
- a negative test that omits those commits and confirms the tracer fails
  closed before publishing evidence;
- one aggregate run plus 12 runtime runs registered in `State`, with every
  event ledger verified against its expected head hash;
- a closed `TraceGraph` containing all 12 RUN→ART→GRADE chains and a canonical
  objective-to-promotion trace;
- a hermetic ClaimGate bundle capped at L1, both improvement flags false, and
  exact refusal of both L2 and L3;
- a treatment wire-cut test showing that disabling supervisor orchestration
  breaks arm B without breaking compute-matched arm C.

## Explicit Claim Boundary

This is deterministic integration evidence, not operational outcome evidence.
It reaches only L1 because the runtime transports are deterministic fixtures
and the hidden verifier has the same producer and verifier principal
(`independent: false`). The run proves that the L1 ClaimGate boundary composes.
It does not establish real task quality, provider behavior, causal
improvement, portability, ROI, or safe auto-improvement.

The tests do **not** run or claim:

- Claude Code CLI or Claude Agent SDK execution;
- Codex CLI execution;
- SWE-bench or its official verifier;
- Unity Editor or Unity Test Framework;
- an externally independent production verifier;
- an OS-level sandbox against a malicious runtime;
- randomized or powered B-vs-C efficacy evidence.

L2 requires genuinely independent verification of operational executions.
L3 additionally requires the preregistered, powered confirmation evidence
defined outside this hermetic slice.

## Wider Worktree Gate Status

This receipt does not claim repository-wide readiness. Current whole-worktree
verification is owned by `docs/program/harness-v1/verification-20260713.md`;
this slice records only the focused commands and evidence above.
