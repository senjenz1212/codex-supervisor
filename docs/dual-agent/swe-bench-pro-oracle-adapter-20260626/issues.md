# Issues

Task id: `swe-bench-pro-oracle-adapter-20260626`

## 1. Propagate Pro Oracle Fields Into The Existing Adapter Context

PRD promise

P1, P3

Public boundary

`swebench_mergeability_official_replay_runner(...)` through `swebench_mergeability_replay_runner(...)` with a fake `oracle_runner` observing context after freeze.

Chosen seam

`_official_hidden_oracle(...)` -> candidate hidden fields -> `adapter_context`.

First RED test

`test_pro_runner_outcome_feeds_interpret_contract` first asserts the adapter context can carry normalized Pro fields without exposing them in frozen/public artifacts.

Allowed outcomes

Lowercase and uppercase Pro labels are normalized to lists, setup and selected-test fields are available only to the adapter, and public packets stay clean.

Forbidden outcomes

Character-splitting JSON strings, leaking selected tests into reviewer packets, or adding a new replay architecture.

## 2. Compute Pass/Fail From Pro Parser Output

PRD promise

P1, P2

Public boundary

`run_swe_bench_pro_oracle(context)` as an `oracle_runner` callable.

Chosen seam

Parser JSON `tests[]` -> required `fail_to_pass`/`pass_to_pass` subsets -> bridge status contract.

First RED test

`test_pro_runner_returns_pass_status_on_gold_fixture`.

Allowed outcomes

All required fail-to-pass and pass-to-pass tests present with `PASSED` status yields both `pass`.

Forbidden outcomes

Treating empty output, skipped output, or missing required tests as `pass`.

## 3. Fail Closed On Pull, Container, Script, Or Parse Failure

PRD promise

P1, P2

Public boundary

`run_swe_bench_pro_oracle(context)`.

Chosen seam

Docker command results and output artifact parsing below the adapter seam.

First RED test

`test_pro_runner_unavailable_on_pull_or_parse_failure`.

Allowed outcomes

Unavailable result with a stage-specific reason and receipt hashes.

Forbidden outcomes

Raising for ordinary runtime failures or fabricating `pass` when output is missing.

## 4. Preserve Patch-Before-Test Ordering

PRD promise

P2, P3

Public boundary

Generated Pro entryscript captured in the adapter receipt.

Chosen seam

Entryscript construction from `base_commit`, `model_patch`, and `before_repo_set_cmd`.

First RED test

Assert the entryscript applies `/workspace/patch.diff` before the Pro setup command and does not replay a reset command after applying the patch.

Allowed outcomes

Only the test-file checkout/setup command needed for oracle tests runs after patch application.

Forbidden outcomes

Running the full multi-line setup field after patch application and erasing the candidate patch.

## 5. Pull And Run Public Pro Docker Images

PRD promise

P2

Public boundary

Adapter receipt from a real or faked Docker run.

Chosen seam

`docker pull jefzda/sweap-images:{dockerhub_tag}` and `docker run` with mounted workspace.

First RED test

Assert Docker commands include the image from `dockerhub_tag`, workspace mount, entryscript path, and timeout.

Allowed outcomes

Docker invocation is recorded with return code, stdout/stderr hashes, image, and artifact paths.

Forbidden outcomes

Using Modal, rebuilding images through SWE-bench, or omitting the image tag from the receipt.

## 6. Vendor Pinned Pro Scripts For The First Artifact

PRD promise

P2

Public boundary

Adapter script lookup for the three selected AEB-0 instance ids.

Chosen seam

`supervisor/vendor/swe_bench_pro/run_scripts/{instance_id}/run_script.sh` and `parser.py`.

First RED test

Assert missing scripts produce `oracle_unavailable_reason="pro_script_missing"` and existing pinned scripts are copied into the workspace.

Allowed outcomes

Three pinned script/parser pairs are available for the real artifact.

Forbidden outcomes

Downloading scripts silently at runtime or vendoring unpinned mutable content.

## 7. Preserve Freeze And Leak Invariants With Lowercase Pro Fields

PRD promise

P3

Public boundary

Official replay hidden-field leak check and frozen decisions.

Chosen seam

`_scan_for_swebench_pro_oracle_refs(...)` and official replay public artifact scan.

First RED test

Add lowercase Pro hidden fields to a record and prove frozen decisions and reviewer packets do not contain them.

Allowed outcomes

Leak scan treats lowercase and uppercase oracle labels, test patches, selected test files, and Pro setup commands as hidden.

Forbidden outcomes

Only protecting uppercase labels while lowercase Pro fields leak.

## 8. Strip Git History From Public Materialized Bundles

PRD promise

P3

Public boundary

`swebench_mergeability_official_replay_runner(...)` using the default official repo materializer.

Chosen seam

`_default_official_repo_materializer(...)` after checkout and before the returned public bundle path is recorded.

First RED test

Create a local fixture repo with a future commit, tag, and remote ref, run the materializer, and assert the returned public bundle contains no `.git` directory and no reachable future-history refs.

Allowed outcomes

The returned public bundle is a detached source tree at `base_commit`.

Forbidden outcomes

Publishing a full clone as a public bundle or relying only on `.git/` protected-path copying later.

## 9. Validate Pro Receipts As Real Oracle Adapter Receipts

PRD promise

P1, P2

Public boundary

`_validate_official_oracle_receipts(...)` on official replay output.

Chosen seam

The nested `official_oracle_adapter` receipt produced by `oracle_runner`.

First RED test

Adapter receipt lacking Docker/image/parser metadata suppresses metrics.

Allowed outcomes

Receipt includes command, return code, hashes, artifact paths, image metadata, parser source, and matching statuses.

Forbidden outcomes

Bare labels or internally inconsistent receipt status passing validation.

## 10. Produce The Three-Instance Execution Artifact

PRD promise

P2

Public boundary

Packet artifact JSON under `docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/artifacts/`.

Chosen seam

Direct adapter execution for the three AEB-0 gold-patch contexts.

First RED test

Not a unit test. Acceptance is a committed artifact that records `attempt_stage`, per-instance image, candidate hash, result, and real unavailable stage if blocked.

Allowed outcomes

Completed-capable oracle rows, or honest blocked rows with exact stage.

Forbidden outcomes

Calling a failed pull or parse a benchmark pass.

## 11. Keep Verified Official Replay Untouched

PRD promise

P1

Public boundary

Existing official Verified adapter tests in `tests/test_swe_bench_pro_mergeability_bridge.py`.

Chosen seam

`run_official_harness_oracle(...)` remains the Verified adapter.

First RED test

Regression selection around existing official harness oracle tests stays green after the Pro adapter lands.

Allowed outcomes

Verified path still invokes `swebench.harness.run_evaluation`.

Forbidden outcomes

Changing Verified default behavior to Pro Docker execution.

## 12. Commit And Gate The Source/Test Slice

PRD promise

P1, P2, P3

Public boundary

`git status --porcelain`, focused pytest, and no-mistakes gate.

Chosen seam

Committed source and tests before running no-mistakes.

First RED test

No separate RED. Acceptance requires source and tests committed together, packet artifacts committed separately or intentionally included, and no-mistakes run only on a clean committed worktree.

Allowed outcomes

Focused tests pass, source/tests commit is scoped, and no-mistakes either passes or reports a real blocker.

Forbidden outcomes

Blanket-committing scratch, unrelated stashes, or out-of-scope benchmark-to-policy bridge code.

## Accepted Ledger Record

```jsonl
{"stage":"issues_review","task_id":"swe-bench-pro-oracle-adapter-20260626","status":"accepted","issue_count":12,"evidence":["docs/dual-agent/swe-bench-pro-oracle-adapter-20260626/issues.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
