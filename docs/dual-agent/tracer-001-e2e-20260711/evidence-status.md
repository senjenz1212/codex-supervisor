# Evidence Status: TRACER-001

## Status

**GREEN for hermetic composition only. Operational/external efficacy is NOT
RUN and is not claimed.**

## Dedicated Hermetic Proof

Command:

```text
.venv/bin/python -m pytest -q tests/test_tracer_001_e2e.py
```

Latest result:

```text
run 1: 1 passed in 2.54s
run 2: 1 passed in 2.60s
run 3: 1 passed in 2.52s
```

The focused test proves that the existing public seams compose locally:

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
- one aggregate run plus 12 runtime runs registered in `State`, with every
  event ledger verified against its expected head hash;
- a closed `TraceGraph` containing all 12 RUN→ART→GRADE chains and a canonical
  objective-to-promotion trace;
- a hermetic ClaimGate bundle capped at L2, both improvement flags false, and
  the exact L3 refusal:
  `asserted claim level L3 exceeds evidence support L2`.

## Explicit Claim Boundary

This is deterministic integration evidence, not operational outcome evidence.
The L2 result applies only to the hermetic fixture bundle and proves the
ClaimGate boundary composes. It does not establish real task quality,
provider behavior, causal improvement, portability, ROI, or safe
auto-improvement.

The test does **not** run or claim:

- Claude Code CLI or Claude Agent SDK execution;
- Codex CLI execution;
- SWE-bench or its official verifier;
- Unity Editor or Unity Test Framework;
- an externally independent production verifier;
- an OS-level sandbox against a malicious runtime;
- randomized or powered B-vs-C efficacy evidence.

## Wider Worktree Gate Status

The shared worktree is under concurrent development and its broader related
suites are not currently all green:

- `tests/test_arm_executor.py` reported **7 passed** after its concurrent
  implementation landed;
- the remaining runtime/task/experiment/grade/ledger/trace sweep reported
  **68 passed, 1 failed**; the failure is a concurrent expectation mismatch
  between `tests/test_grade_revisions.py` and the unowned `Grade` constructor,
  which now rejects an empty verifier version before `GradeBook` is called;
- `tests/test_claim_gate.py` reported **10 passed, 5 failed** because the
  concurrently edited, unowned `supervisor/claim_gate.py` references an
  undefined `_strict_json_loads`.

Those failures were not modified or masked by TRACER-001. The dedicated
hermetic tracer test is green, while the repository-wide component gate
remains blocked on those unowned concurrent changes.
