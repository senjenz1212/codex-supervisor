# Harness v1 Post-Audit Hardening — July 15, 2026

## Scope

This report closes the concrete branch-level defects found after the
`c04d60f9edba37753229a05e53b79da001b9ee30` verification checkpoint. It does
not raise the Harness v1 claim ceiling above L1 and does not represent an
operational pilot, efficacy result, ROI result, or auto-improvement result.

## Finding Ledger

| ID | Finding | Resolution | Commit | Focused proof |
|---|---|---|---|---|
| PAH-001 | In-process agentic workers persisted the shared supervisor host PID as a cleanup target. A second process could terminate the whole host while cleaning one worker. | Shared-host records now persist no killable PID. Cleanup signals only records explicitly scoped to a dedicated process and still requires matching PID creation time. Legacy or unspecified scope fails closed. | `8d1716d0fd9491eaaa6c0be4a851fcc063dd9957` | `tests/test_agentic_workers.py`: 18 passed |
| PAH-002 | `ThreadPoolExecutor` workers remain joined by Python at interpreter exit, so an uncooperative runtime could defeat bounded KeyboardInterrupt cleanup. | The synchronous bridge now uses a dedicated daemon thread, propagates normal results and exceptions, requests bounded cancellation, and can abandon an uncooperative worker without blocking interpreter exit. | `777d20e65a13a65ef6812fde43cb3b927cb4693b` | `tests/test_runtime_execution.py`: 3 passed |
| PAH-003 | Untimed tool calls at the same list position in distinct envelopes could receive the same synthesized ID and be merged in run-wide totals. | Untimed synthesized IDs now include a deterministic envelope scope. Replaying the same envelope is stable; distinct envelopes differ; explicit or timed logical-call IDs remain unchanged. | `e6790dea900e1231475f04c4fa62895d491a3145` | focused trace-envelope tests: 5 passed |
| PAH-004 | Terminal runtime generations could be evicted before the owner harvested their result. | Runtime and transport retention now evict only successfully collected terminal generations. Resume clears harvest authority until the resumed generation is collected. Same-ID Claude SDK replacement is blocked until collection. | `4c3a88522bb84f9cc817690abef1696115be9110` | `tests/test_agent_runtime.py`: 68 passed; `tests/test_claude_sdk_runtime.py`: 17 passed |

## Merge-Hygiene Repairs

- SQLite `-wal` and `-shm` sidecars are ignored alongside their database files.
- Seven pre-existing extra blank lines at EOF are removed.
- The older 2,641-test verification receipt is labeled as an earlier
  checkpoint rather than the final result.
- The EXP-001 roadmap result is named an efficacy-experiment kernel, not an
  efficacy result.

## Review Follow-Up

The no-mistakes review of this hardening landed one follow-up commit,
`b594d022a87954085b41e59168a69c6c6f1647d9`:

- A containment-proof failure that follows a runtime timeout keeps
  `reason="timeout"` on the terminal event and records the containment error
  in separate `containment_reason`/`containment_error` fields instead of
  overwriting the timeout taxonomy.
- The unused `verify_authoritative_event_chain` re-export is removed from
  `supervisor.evidence_ledger`; the persisted-checkpoint API in
  `supervisor.ledger_checkpoints` remains the only verification entry point.
- The fail-closed cleanup scope from PAH-001 is documented on
  `cleanup_orphaned_agentic_workers`.

Focused proof at that commit: `tests/test_claude_sdk_runtime.py` plus
`tests/test_agentic_workers.py`, 36 passed.

## Claim Boundary

These changes improve process safety, cleanup authority, accounting identity,
and result retention. They do not establish:

- an independently graded operational outcome (L2);
- randomized powered B-vs-C improvement (L3);
- portability across model and task strata (L4);
- positive ROI (L5); or
- safe governed auto-improvement (L6).

The next claim-bearing milestone remains `TRACER-001-OPERATIONAL`, followed by
a disjoint PILOT-001 and powered CONFIRM-001.

## Integration Verification

Pre-commit verification at
`4c3a88522bb84f9cc817690abef1696115be9110`:

```text
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

The no-mistakes receipt is recorded after these documentation and hygiene
changes are committed. None of these integration checks raises the claim
ceiling above L1.
