# Outcome Evidence Packet

This reviewer-safe packet summarizes the corrected official SWE-bench Verified replay smoke without copying hidden oracle content into the review prompt. The raw replay report and manifest remain local artifacts for verification, while this packet names stable paths, hashes, and non-claim flags.

## Corrected Real Smoke

- Dataset: `SWE-bench/SWE-bench_Verified`, split `test`.
- Selected instance count: `1`.
- Candidate count: `1`.
- Official oracle adapter: `supervisor.swe_bench_official_oracle:run_official_harness_oracle`.
- Harness command family: `python -m swebench.harness.run_evaluation`.
- Official replay report: `docs/dual-agent/swebench-verified-replay-smoke-oracle-floor-20260622/official-smoke/replay-output/official_replay_report.json`.
- Official replay report sha256: `b343cffc18a0c94471d27210adb7484e8dc39cca0f92f4fb297511c409ab94af`.
- Official replay manifest sha256: `c319cba6b9a35aa222a1f07ce84406ed04490ab0bcb15eb1388bb35b0c4a91ce`.
- Frozen decisions sha256: `fc22d53361195997f89e66448ae5339187944fb8a50c82e4c98d546f5b24edca`.
- Oracle outputs sha256: `3b1ea6224e8f67ad25d43d79fe217190ae9808ec37fecb92972b203a1ffae38b`.
- Artifact index sha256: `b527d87f2d93e5aa11e6c6213b900290ed3761d0b8b63e64b6b877e72ecb7ae8`.

## Reviewer-Safe Verification Notes

- `oracle_receipt_validation.validated` is `true`.
- `hidden_field_leak_check.ok` is `true`.
- `metric_applyable` is `false`.
- `improvement_claim_allowed` is `false`.
- `default_change_allowed` is `false`.
- `policy_mutated` is `false`.
- `gate_advanced` is `false`.
- `plumbing_smoke_only` is `true`.
- `powered_improvement_claim_allowed` is `false`.
- `human_mergeability_claim_allowed` is `false`.

## Corrections After Blocked Review

- The official replay manifest now preserves official hidden test lists as lists, not character arrays.
- Public candidate worktrees exclude `.git/` internals and stale target worktrees are removed before copying.
- The smoke was regenerated after both fixes; the current artifact hashes above supersede the blocked-review hashes.
- Large public checkout and copied candidate worktree directories remain gitignored; compact receipts and report artifacts remain present for local verification.
