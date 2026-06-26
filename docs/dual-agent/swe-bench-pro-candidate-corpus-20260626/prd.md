# SWE-bench Pro Candidate Corpus PRD

Task id: `swe-bench-pro-candidate-corpus-20260626`

Depends on: `swe-bench-pro-oracle-adapter-20260626`

## Problem Statement

The current SWE-bench Pro bridge can only measure false-accept rate honestly when the evaluated predictions include oracle-negative candidates. A gold-only Pro predictions file makes `n_bad == 0`, and the shared `_rate(...)` helper returns `0.0` for an empty denominator. That makes the headline FAR metric a constant rather than a measured safety rate. SWE-bench Verified and SWE-bench Pro ship gold patches, not bad model patches, so the missing corpus cannot be recovered from the dataset alone.

## Solution

Create a report-only candidate-corpus builder that writes JSONL consumable by `_load_official_predictions(...)`, preserves per-candidate provenance and diff hashes, and bins candidates only from the Slice-1 Pro oracle outcome. Gold candidates can supply oracle-good examples. Oracle-bad candidates must come from real single-agent solver attempts and must be kept only when the candidate patch applies and the Pro oracle returns non-resolving test status. If live solver or Pro oracle execution is unavailable, the execution artifact must say blocked/unavailable instead of producing a fake `pro-predictions.jsonl`.

## PRD Promise Contracts

### P1. Corpus carries oracle-good and oracle-bad candidates with provenance.

Public boundary

`_load_official_predictions(...)` in `supervisor/swe_bench_mergeability.py` accepts JSONL rows that contain `instance_id`, `model_patch`, `candidate_artifact_hash`, model-patch hash, candidate origin, producer provenance, and optional baseline receipt keys.

Chosen seam

JSONL prediction lines with `instance_id`, `candidate_id`, `model_patch`, `candidate_artifact_hash`, `model_patch_sha256`, `origin`, `producer`, and optional `single_agent_baseline_decision`/`produced_baseline_decision`/`baseline_decision`.

Allowed outcomes

- A corpus JSONL with at least two `oracle-good` candidates and at least one `oracle-bad` candidate.
- Every candidate has a unique `candidate_artifact_hash` and a stable diff hash.
- Loader output preserves provenance and receipt fields for downstream official replay.
- If real generation/oracle execution is blocked, the artifact is explicitly blocked and no FAR-capable corpus is claimed.

Forbidden outcomes

- Synthetic, hand-edited, or fixture-only bad patches presented as real solver candidates.
- A gold-only corpus presented as FAR-capable.
- Dropping producer provenance or diff hashes at load time.
- Setting any authority flag to true.

### P2. Binning is oracle-grounded.

Public boundary

The candidate-corpus generator's bin step: good means the real Pro oracle resolves; bad means the candidate patch applies but the real Pro oracle does not resolve.

Chosen seam

A generator/corpus function that receives candidate attempts, invokes the Slice-1 Pro oracle below the boundary, and writes only oracle-labeled `oracle-good`/`oracle-bad` rows. Live solver and Docker are dependencies below this seam and may be faked only in unit tests.

Allowed outcomes

- `oracle-good` when `fail_to_pass_status == "pass"` and `pass_to_pass_status == "pass"`.
- `oracle-bad` when the patch is known to apply and at least one required oracle bucket is `fail`.
- Non-applying, missing-output, malformed-output, timeout, Docker, ENOSPC, or unavailable cases are excluded from the FAR denominator and recorded in the attempt report.

Forbidden outcomes

- Labeling bad by public tests, solver self-report, reviewer judgment, or any source other than the Slice-1 Pro oracle.
- Counting non-applying patches as oracle-bad.
- Treating unavailable oracle runs as negative examples.

## User Stories

1. As the benchmark operator, I want a predictions JSONL that includes oracle-good and oracle-bad candidates, so that FAR has a real denominator.
2. As the benchmark operator, I want every candidate to carry origin and diff-hash provenance, so that later reports can distinguish gold replay from generated solver attempts.
3. As the benchmark operator, I want bad candidates labeled only by the Pro oracle, so that the corpus cannot smuggle in public-test or reviewer guesses.
4. As the benchmark operator, I want non-applying and unavailable candidates excluded, so that `n_bad` means patch-applies-but-tests-fail rather than infrastructure failure.
5. As the benchmark operator, I want blocked execution recorded honestly, so that a host without Docker/disk/solver capacity does not fabricate a FAR-capable corpus.

## Implementation Decisions

- Extend the official predictions loader to preserve candidate provenance and compute missing stable hashes.
- Add a focused candidate-corpus builder rather than changing FAR math or reviewer-panel logic.
- Use the existing Slice-1 Pro oracle result contract for labels; do not invent a separate oracle.
- Keep real execution evidence under this packet's `artifacts/` directory.
- Keep all authority flags false: `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced`.

## Testing Decisions

- The first RED test exercises `_load_official_predictions(...)` with good and bad rows plus provenance.
- The second RED test proves a fixture corpus with at least one oracle-bad row produces `n_bad > 0`, so FAR is not degenerate.
- The third RED test exercises the generator/binning boundary with solver/oracle faked below the public seam.
- Real solver/Docker/oracle execution is artifact evidence, not a unit test, and must be blocked rather than mocked when unavailable.

## Out of Scope

- Powering math and minimum sample-size claims.
- Reviewer-panel changes.
- Autonomous benchmark-to-policy bridge work.
- Any policy/config mutation.
- Treating report-only artifacts as AutoResearch promotion evidence.

## Further Notes

The METR mergeability note is relevant because it frames why test-passing candidate patches are not automatically mergeable, but this slice only makes the Pro false-accept denominator measurable. It does not claim mergeability improvement, human mergeability, policy mutation, or default-change authority.
