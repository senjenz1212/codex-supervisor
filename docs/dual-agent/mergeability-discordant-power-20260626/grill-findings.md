# PRD Grill Findings

Gate: `prd_review`

Decision: accepted after revisions below.

## Finding 1: Raw denominator sufficiency could still be mistaken for the power gate.

Severity: blocking before revision.

Evidence:

- `supervisor/mergeability_bench.py:2007` builds `sample_size_sufficiency`.
- `supervisor/mergeability_bench.py:2018` derives `powered` from the sample-size status.
- `supervisor/mergeability_bench.py:5370` only checks raw `n_bad` and `n_good`.

Resolution:

The PRD names `paired_power` as the public authority for paired sufficiency and keeps raw sample size as descriptive denominator evidence.

Status: resolved.

## Finding 2: The paired seam existed but was not decision-bearing.

Severity: blocking before revision.

Evidence:

- `supervisor/mergeability_bench.py:1993` computes paired discordant counts.
- `supervisor/mergeability_bench.py:4952` returns McNemar cells.
- No current powered gate consumes those cells.

Resolution:

The PRD chooses `_paired_power_sufficiency` as the small interface that turns existing discordant cells into a reportable paired test and gate input.

Status: resolved.

## Finding 3: CI promise must include FRR, not only FAR/TAR.

Severity: medium.

Evidence:

- `supervisor/mergeability_bench.py:1968` adds the powered factorial FRR interval after arm summarization.
- `supervisor/swe_bench_mergeability.py:695` projects `far_tar_frr` with FAR/TAR interval fields but not FRR interval fields.

Resolution:

The PRD explicitly names FAR, TAR, and FRR as interval-bearing public rates and forbids missing FRR interval fields.

Status: resolved.

## Finding 4: Authority flags must remain outside this packet.

Severity: medium.

Evidence:

- Prior repo memory records that benchmark reports remain diagnostic/report-only unless a separate AutoResearch bridge produces accepted `records[]`.
- The current report already keeps `improvement_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced` false.

Resolution:

The PRD out-of-scope section and promise contracts preserve false authority flags and explicitly exclude the autonomous benchmark-to-policy bridge.

Status: resolved.
