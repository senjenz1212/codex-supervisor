# Issues

Task id: `pro-single-agent-solver-20260626`

## 1. Define The Public Generator Contract

PRD promise

P1, P2, P3

Public boundary

`SWEBENCH_MERGEABILITY_GENERATOR_INPUT` and `SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT`.

Chosen seam

`supervisor.swe_bench_solver` CLI.

Tracer bullet

Read input JSON from the env var, validate it is public-only, and write an output JSON object.

Allowed outcomes

The generator runs only with explicit live consent and writes parseable output.

Forbidden outcomes

Reading hidden oracle fields or writing non-JSON output.

## 2. Add Public Packet Isolation Guard

PRD promise

P1

Public boundary

Generator input validation.

Chosen seam

A public-only input sanitizer in the solver module.

Tracer bullet

Reject input containing `FAIL_TO_PASS`, `PASS_TO_PASS`, `oracle_accept`, `expected_outcome`, `final_score`, hidden commands, or hidden test patches.

Allowed outcomes

Fail closed before any runner invocation.

Forbidden outcomes

Passing hidden fields into the prompt or agent runner.

## 3. Materialize Per-Attempt Worktrees

PRD promise

P1, P3

Public boundary

Generator output `attempts`.

Chosen seam

One public worktree copy per attempt.

Tracer bullet

Prepare a clean attempt directory from the public worktree for every attempt.

Allowed outcomes

Each attempt starts from the same public state.

Forbidden outcomes

Attempt N inherits unstaged changes from attempt N-1.

## 4. Run One Agent K Times

PRD promise

P1

Public boundary

Generator invocation with `--k`.

Chosen seam

Injected runner callable in tests, real runner command in live mode.

Tracer bullet

Invoke the same runner `k` times with attempt index and public packet metadata.

Allowed outcomes

`k > 1` produces `k` runner calls and `k` attempt records when all attempts yield diffs.

Forbidden outcomes

Panel behavior, multiple different agents, or silently reducing `k` to one.

## 5. Capture Model Patches

PRD promise

P1

Public boundary

Per-attempt `model_patch`.

Chosen seam

`capture_model_patch(...)`.

Tracer bullet

After the runner finishes, capture `git diff --binary HEAD --` from the attempt worktree.

Allowed outcomes

Non-empty diffs are retained with stable hashes.

Forbidden outcomes

Static fixture diffs in live mode or empty diffs reported as successful attempts.

## 6. Emit Trusted Baseline Receipts

PRD promise

P2

Public boundary

`single_agent_baseline_decision`.

Chosen seam

Receipt builder in the solver module.

Tracer bullet

Build a receipt whose hash matches the captured patch and whose producer/prompt evidence is replayable.

Allowed outcomes

The resolver accepts hash-matched trusted receipts.

Forbidden outcomes

Legacy bools or mismatched hashes pass as available.

## 7. Preserve Rejected Attempts

PRD promise

P3

Public boundary

Output `attempts`.

Chosen seam

Attempt normalization.

Tracer bullet

Include attempts even when the single agent `accept=false`.

Allowed outcomes

Rejected non-empty diffs remain available for oracle labeling.

Forbidden outcomes

Filtering down to only accepted or resolving attempts.

## 8. Keep Existing Single-Candidate Compatibility

PRD promise

P1

Public boundary

Existing live runner normalization.

Chosen seam

Top-level `model_patch` and `single_agent_baseline_decision` mirror attempt 1.

Tracer bullet

Write the first attempt at top level while retaining the full attempts list.

Allowed outcomes

Existing `_command_generator` consumers still receive a patch-shaped mapping.

Forbidden outcomes

Breaking current live replay paths while adding multi-attempt output.

## 9. Add Corpus Hand-Off Helper

PRD promise

P3

Public boundary

Attempts fed to `build_swe_bench_pro_candidate_corpus(...)`.

Chosen seam

A flattening helper or documented output shape that exposes the attempts list.

Tracer bullet

Ensure every attempt has the fields the corpus builder expects.

Allowed outcomes

The corpus builder can label accepted and rejected non-empty diffs.

Forbidden outcomes

Dropping baseline receipts or provenance before corpus labeling.

## 10. Produce Real Execution Artifact

PRD promise

P1, P2, P3

Public boundary

Committed artifact under this packet.

Chosen seam

A small live run or honest blocked manifest.

Tracer bullet

Run the generator with live consent and record the exact output or blocker.

Allowed outcomes

Committed k-run artifact with non-empty diffs, or honest unavailable artifact with authority flags false.

Forbidden outcomes

Fixture output represented as live execution.

## 11. Run No-Mistakes Gate

PRD promise

P1, P2, P3

Public boundary

Committed source, tests, packet, and artifacts.

Chosen seam

`no-mistakes axi run`.

Tracer bullet

Commit the slice and validate it through no-mistakes.

Allowed outcomes

Gate passes or reports exact blockers.

Forbidden outcomes

Running no-mistakes on a dirty worktree or ignoring ask-user findings.
