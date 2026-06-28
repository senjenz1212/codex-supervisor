# Tri-Agent Findings

Task id: `pro-oracle-gold-proof-20260626`

## Validator A: Real-Attempt Honesty

Verdict: revise.

Strongest objection: the plan required `patch_applied=true`, but the current adapter can still claim patch applied when `patch_apply.json` is missing or malformed. `_pro_patch_applied(...)` returns `None`, the caller only fails on `False`, and the success path writes `patch_applied=True` into the receipt.

Required foldbacks:

- RED 1 must validate raw `patch_apply.json.patch_applied is true`, not only top-level result fields.
- Missing or malformed patch-apply proof must become unavailable.
- The artifact manifest must include patch, run script, parser, entryscript, output, test-command, and patch-apply evidence.
- The `before_repo_set_cmd` ordering risk must be framed narrowly: destructive repo resets must not erase the candidate patch, while hidden-test checkout semantics must be preserved.

## Validator B: Oracle Isolation And Correctness

Verdict: revise.

Strongest objection: empty parser output and non-empty failing parser output currently collapse into the same empty passed-test set. The fix must preserve parsed test count so `{"tests":[]}` can be unavailable while non-empty oracle-bad remains ordinary fail.

Required foldbacks:

- The empty-output guard must call `run_swe_bench_pro_oracle(...)` and fake only Docker/subprocess below the adapter boundary.
- The implementation must use parsed tests length, not only the passed-test set.
- Existing non-empty oracle-bad behavior must remain valid.
- All authority flags must stay false.

## Validator C: VM Reproducibility And Artifact Handling

Verdict: revise.

Strongest objection: the packet did not yet specify a deterministic Bokken runner or exact runbook. A loose one-off invocation could load incomplete row fields, write to `.scratch`, or leave the proof only on the VM.

Required foldbacks:

- Add a deterministic runner/runbook that pins an instance id, loads the real dataset row, maps dataset `patch` to adapter `model_patch`, and verifies non-empty `fail_to_pass`, `pass_to_pass`, and selected tests.
- Commit a VM preflight artifact with architecture, Docker version/server architecture, platform env, Docker storage, and disk.
- Commit a manifest with command/env, receipt, file hashes, parsed-test count, selected instance metadata, and all authority flags false.
- The fixture test must require non-empty required buckets as well as non-empty parsed tests.

## Foldback Decision

All required changes are accepted. The packet is revised before implementation, and implementation must include:

- Patch-apply receipt fail-closed behavior.
- Empty parser output fail-closed behavior using parsed test count.
- A deterministic proof runner or equivalent committed runbook.
- Real Bokken receipt artifacts under this packet's `artifacts/` directory.
