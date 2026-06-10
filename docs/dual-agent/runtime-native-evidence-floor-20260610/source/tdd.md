# Runtime-Native Evidence Floor TDD

## test_execution_gate_rejects_fabricated_runtime_receipts_for_missing_file

Maps to: Slice 1, Slice 2, P2, P3, P5

Red: Build a workflow outcome that declares `supervisor/fake.py` and supplies fabricated agent git/test receipts while the file does not exist in the worktree. The execution gate should currently be able to treat agent receipts as enough evidence.

Green: Runtime evidence records the real git status and deliverable check, reports the missing file, and blocks execution with runtime-native failure details.

## test_outcome_review_requires_supervisor_rerun_for_tests_passed_claim

Maps to: Slice 1, Slice 2, P4, P5

Red: Build an outcome review claim of `tests passed` with an agent-supplied passing receipt for a command that actually exits non-zero. The final claim verifier should not accept that self-report.

Green: The supervisor reruns the declared command in the validation copy, emits a failed runtime-native test receipt, and blocks outcome review.

## test_execution_gate_accepts_supervisor_runtime_native_receipts

Maps to: Slice 1, Slice 2, P1, P2, P3, P4, P5

Red: Create a tiny repository with real changed source and test files after baseline; without runtime-native receipts, the accepted execution path cannot satisfy the new evidence floor.

Green: The supervisor captures baseline, observes changed files, verifies deliverables, reruns the test command, emits runtime-native receipts, and allows the gate to advance.

## test_read_gate_transcript_includes_runtime_evidence_events

Maps to: Slice 3, P6

Red: After a workflow run, the gate transcript lacks a durable runtime evidence event, leaving reviewers to inspect only in-memory receipts or agent claims.

Green: `read_gate_transcript` returns `runtime_evidence` entries sourced from `dual_agent_runtime_evidence` ledger events.

## test_agent_supplied_tests_passed_receipt_without_supervisor_rerun_fails

Maps to: Slice 2, P4, P5

Red: An outcome claims `tests passed` with no declared command for the supervisor to rerun and only an agent-supplied receipt.

Green: Runtime evidence emits `runtime_test_command_missing`, and claim verification rejects the claim without a supervisor-owned test receipt.
