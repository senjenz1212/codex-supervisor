# Tri-Agent Findings

Task id: `pro-single-agent-solver-20260626`

## Validator A: Real Generation

Verdict: revise.

Strongest objection: the planned first RED called `swe_bench_solver.main(...)` directly, but the public boundary is the CLI `_command_generator(...)` shell seam that writes input/output env-var paths. The current solver also still reaches the live-execution refusal after consent and budget.

Required foldbacks:

- Add a public-boundary RED that invokes `_command_generator(...)` with the solver command and asserts the returned JSON contains `attempts`, top-level compatibility `model_patch`, and receipt fields.
- Keep no-consent and no-budget refusals, but ensure live consent plus budget plus `k > 1` and a runner command returns success.
- Fake runner tests must mutate real git attempt worktrees, then prove `capture_model_patch(...)` captured distinct diffs and attempts do not inherit prior attempts.
- Decide and test untracked file behavior. This slice will include untracked files by marking intent-to-add before diff capture.

## Validator B: Receipt Trust

Verdict: revise.

Strongest objection: P2 requires `producer.provider`, but `_resolve_powered_baseline_decision(...)` does not currently require provider, and one powered-factorial call does not pass `expected_candidate_id`.

Required foldbacks:

- Require provider evidence in the resolver rather than weakening P2.
- Require candidate id, model, and runner label replay evidence consistently, not only when a caller passes `expected_candidate_id`.
- Pass `expected_candidate_id` in the powered-factorial resolver call.
- Add tests for hash mismatch and missing provider/model/runner/candidate id on the generator-produced receipt path.

## Validator C: Isolation And Retention

Verdict: revise.

Strongest objection: the repo has public-input scanners in adjacent paths, but `swe_bench_solver.py` does not yet validate hidden oracle fields or emit attempts. Existing live normalization is single-candidate-shaped and would drop an `attempts` list.

Required foldbacks:

- Implement solver env-var generator mode with public-only packet validation before runner invocation.
- Add a hidden-field rejection test proving the runner is not called.
- Emit `attempts` including `accept=false` baseline decisions and first-attempt top-level compatibility fields.
- Add a flattening handoff so generator attempts can be fed to `build_swe_bench_pro_candidate_corpus(...)` without dropping rejected/non-resolving attempts.
- Preserve baseline receipts when corpus rows are written.

## Foldback Decision

All required changes are accepted. Implementation must include:

- `_command_generator(...)` boundary coverage.
- Public-only solver input validation.
- Real per-attempt git worktree mutation and diff capture in tests.
- Trusted receipt resolver hardening.
- Attempts-to-corpus flattening and corpus receipt preservation.
- Honest execution artifact or blocked artifact with all authority flags false.
