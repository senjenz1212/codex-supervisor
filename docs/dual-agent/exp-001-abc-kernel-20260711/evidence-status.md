# Evidence Status: EXP-001

## Status

**Treatment-identity blocker implemented; no operational efficacy experiment
run.**

## Current Repository Evidence

Focused tests cover:

- persisted deterministic assignment before execution;
- canonical treatment descriptors bind adapter/entrypoint, instruction
  template, and treatment configuration;
- immutable preregistration, assignment, launch metadata, and execution
  receipts persist treatment hashes;
- duplicate treatment hashes, post-assignment mutation, and mismatched
  receipts are rejected;
- B/C treatment hashes differ while compute/resource hashes must match;
- the hermetic tracer executes distinct production-baseline,
  supervisor-orchestration, and compute-matched-direct adapters;
- a wire-cut test proves disabling supervisor orchestration breaks B but not C;
- equal B/C budget validation;
- one A/B/C result envelope;
- recursive metadata plus output-level blinding and post-grade arm join;
- crash-as-ITT failure;
- primary/adjudicator packet shape;
- paired B/C analysis, exact power table, disjointness, and ROI formulas.

Verification command:

```text
.venv/bin/python -m pytest -q \
  tests/test_experiment_kernel.py \
  tests/test_arm_executor.py \
  tests/test_tracer_001_e2e.py
```

Observed result on July 13, 2026: **107 passed**.

## Not Yet Proven

- The tracer uses deterministic local fixture transports, not provider CLIs,
  an operational container backend, SWE-bench, or Unity Test Framework.
- Its verifier is hidden from the runtime workspace but is not independently
  produced; producer and verifier principal remain the same.
- The wire-cut establishes composition sensitivity, not B-over-C efficacy.
- No pilot, causal claim, external review, skill receipt, or commit exists from
  this fixture evidence.

## Claim Boundary

The evidence supports local kernel and hermetic composition contracts at L1.
It does not establish operational arm efficacy, independent evaluation,
real-task causal improvement, or B-over-C superiority.
