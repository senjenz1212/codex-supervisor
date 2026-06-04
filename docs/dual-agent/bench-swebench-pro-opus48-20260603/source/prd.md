# PRD: SWE-bench Pro Opus 4.8 Harness-vs-Baseline Pilot

## Problem Statement

Build report-only adapters for a fixed-model SWE-bench Pro pilot comparing
codex-supervisor's gated dual-agent harness against a minimal mini-swe-agent
baseline. Both arms use Opus 4.8, the same `k=5` attempts per instance, and the
same per-run budget. The pilot emits deterministic planning and replay reports;
it does not change supervisor defaults or enable a full 731-task run.

The repo already has a Terminal-Bench report-only benchmark pattern, but no
SWE-bench Pro solver adapter, no mini-swe-agent baseline plan, and no
SWE-bench-specific patch/report fixtures. Without this slice, operators cannot
make a same-model same-budget decision about whether the gated harness is worth
scaling to the expensive full benchmark.

## Solution

Add a SWE-bench Pro report-only evaluation layer with three public surfaces:

- `supervisor/swe_bench_eval.py` for schema-versioned sample/results loaders,
  pilot plans, pass@ reports, and replay exports.
- `supervisor/swe_bench_solver.py` for codex-supervisor harness and
  mini-swe-agent baseline adapter planning plus `{instance_id, model_patch}`
  patch-row generation.
- `scripts/run_swe_bench_pro_pilot.py` for dry-run plans, fixture-backed
  reports, and live-run refusal unless the caller supplies `--allow-live` plus
  a positive per-run budget.

The implementation mirrors the Terminal-Bench slice's report-only posture:
fixture replay is deterministic and cheap, while live Docker/model execution is
explicit, budget-capped, and detached/checkpointable.

## Grounding Evidence

- SWE-bench Pro OS repository probe: `scaleapi/SWE-bench_Pro-os` main exists
  and contains `swe_bench_pro_eval.py`, `helper_code/gather_patches.py`, and
  `mini-swe-agent`.
- HuggingFace dataset server probe for `ScaleAI/SWE-bench_Pro` test split
  reported `num_rows_total=731`.
- The fixed pilot sample uses seed `20260603`, `sample_size=30`, `k=5`, and
  model `claude-opus-4-8`.

## User Stories

1. As an evaluator, I can load a fixed 30-instance SWE-bench Pro sample and know
   which seed, dataset split, model, and k value produced it.
2. As an operator, I can inspect baseline and harness command plans before any
   paid model or Docker work starts.
3. As a maintainer, I can run a fixture-backed report and see pass@1, pass@5,
   pass^5, delta CI, and noise-floor verdict without live calls.
4. As a harness developer, I can turn a final repository diff into the solver
   JSONL row shape expected by SWE-bench Pro grading.
5. As a repo owner, I can verify this benchmark scaffolding did not mutate
   supervisor defaults or agentic policy.

## PRD Promise Contracts

P1. Harness Solver Adapter.

User-visible promise: The repo exposes a codex-supervisor SWE-bench Pro solver
adapter that can build a per-instance harness run intent, preserve Opus 4.8 as
the lead model route, and emit `{instance_id, model_patch}` JSONL rows from the
final diff.

Public boundary: `swe_bench_solver_adapter`.

Allowed outcomes: dry-run plan rows and diff-capture rows; live runs only after
an explicit budget guard.

Forbidden outcomes: live model/Docker calls by default; patch rows without an
`instance_id`; changing global supervisor config or policy defaults.

P2. Minimal Baseline Adapter.

User-visible promise: The pilot includes a mini-swe-agent baseline adapter on
the same instance/model/budget surface, so the comparison is harness vs strong
minimal scaffold rather than harness vs no harness.

Public boundary: `swe_bench_pilot_plan`.

Allowed outcomes: command/plan artifacts for mini-swe-agent and harness arms.

Forbidden outcomes: using different models or budgets across arms; hardcoding a
baseline win/loss; running the full set before pilot signal.

P3. Fixed Pilot Sample.

User-visible promise: The pilot records a fixed, seed-pinned 30-instance sample
from the 731-row SWE-bench Pro test split, with instance ids committed for
deterministic replay.

Public boundary: `swe_bench_pilot_sample_loader`.

Allowed outcomes: exact schema-versioned sample validation.

Forbidden outcomes: duplicate instance ids; unrecorded seed; sample size that
does not match the committed instance list.

P4. Report-Only Metrics.

User-visible promise: The pilot report computes per-arm mean pass@1 with 95%
CI, pass@5, pass^5, harness-minus-baseline delta with CI, and a 3pt noise-floor
verdict. Published Opus 4.8 69.2% is included as external directional context
only.

Public boundary: `swe_bench_report_builder`.

Allowed outcomes: deterministic fixture/replay report and explicit null or
inconclusive verdict.

Forbidden outcomes: declaring a win when the CI lower bound does not clear the
noise floor; treating the external 69.2% as an apples-to-apples baseline.

P5. Cost Guard and Report-Only Invariant.

User-visible promise: Live pilot execution is guarded by `--allow-live` and a
positive per-run `--max-budget-usd`; default change is disallowed.

Public boundary: `swe_bench_pilot_cli`.

Allowed outcomes: dry-run planning by default; live command execution only
after explicit budget.

Forbidden outcomes: mutating `AgenticLeadCfg`, `supervisor/state.py`, or
production policy defaults; spending budget without an explicit cap.

## Implementation Decisions

- Treat `claude-opus-4-8` as the fixed model string for both arms and verify the
  repo's `quality="best"` Claude route still maps to that underlying model.
- Store the committed pilot as a replay fixture. Live execution plans are
  written first and refused unless a caller opts in with a positive budget.
- Emit solver rows as `{instance_id, model_patch}` and provide
  evaluator-compatible rows with `patch` and `prefix`, since the OS evaluator
  consumes gathered patch dictionaries.
- Keep harness execution under codex-supervisor's existing production config in
  live plans; this slice's own supervised build uses per-call
  `agentic_lead_policy=off` as requested.

## Testing Decisions

- Public-boundary tests cover the sample loader, report builder, solver adapter,
  pilot plan, and pilot CLI.
- Tests use only local fixtures and temporary git repositories; they do not call
  live models, Docker, Modal, HuggingFace, or GitHub.
- Report-only tests compare `AgenticLeadCfg().model_dump()` before and after
  report construction and assert policy/config mutation flags remain false.

## Out of Scope

- No full 731-task run.
- No reproduction of Anthropic's exact harness.
- No Terminal-Bench changes.
- No supervisor policy/default changes.
- No live model, Docker, Modal, or HuggingFace dataset calls in tests.
