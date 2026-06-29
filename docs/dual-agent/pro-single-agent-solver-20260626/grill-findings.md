# PRD Grill Findings

Task id: `pro-single-agent-solver-20260626`

## Verdict

Accepted after tightening the PRD around public-packet isolation, attempt retention, and receipt trust.

## Findings

### G1. The generator must not see hidden oracle fields.

Status: resolved.

Evidence: `build_swe_bench_pro_public_packet(...)` excludes hidden fields, while live runners later construct generator input from public records/worktrees.

Resolution: P1 forbids hidden oracle material in the input and tests require failure if hidden fields appear.

### G2. `k > 1` must be observable in output, not only in logs.

Status: resolved.

Evidence: `_normalise_swebench_live_generation_result(...)` currently consumes one patch-shaped mapping, while the corpus-builder path needs many attempts.

Resolution: P1 requires an `attempts` list and P3 requires retaining all non-empty diffs.

### G3. Baseline receipts must be replayable, not convenience metadata.

Status: resolved.

Evidence: `_resolve_powered_baseline_decision(...)` rejects missing hashes, hash mismatches, legacy booleans, missing producer/model/runner fields, and untrusted decision sources.

Resolution: P2 requires the same replay evidence fields and hash match the resolver checks.

### G4. Live model execution cannot be a unit-test dependency.

Status: resolved.

Evidence: The user explicitly scopes real agent execution as an execution artifact and asks for fakes below the public boundary.

Resolution: TDD tests fake the runner below the generator boundary; the real k-run artifact is separate.

### G5. This slice must not become the corpus oracle.

Status: resolved.

Evidence: `build_swe_bench_pro_candidate_corpus(...)` labels attempts through an injected oracle runner.

Resolution: P3 keeps the generator label-free with respect to oracle-good/bad and preserves oracle isolation.
