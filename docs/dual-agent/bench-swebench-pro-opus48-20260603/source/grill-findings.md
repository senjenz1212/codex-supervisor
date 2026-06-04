# PRD Grill Findings

### Finding 1: The public split size must be grounded, not copied from prompt text

Status: resolved

Resolution: The PRD records a HuggingFace dataset-server probe showing
`num_rows_total=731` for `ScaleAI/SWE-bench_Pro` test. The sample fixture must
store the seed and instance ids so tests never call HuggingFace.

### Finding 2: Same-model comparison must be explicit at adapter boundaries

Status: resolved

Resolution: The solver/report modules must use `claude-opus-4-8` as the fixed
model and verify the repo's `quality="best"` route maps to the Opus 4.8
underlying model. The baseline and harness command plans must carry the same
model string.

### Finding 3: The output shape in the OS evaluator is `patch`, while the solver contract says `model_patch`

Status: resolved

Resolution: The harness solver emits `{instance_id, model_patch}` JSONL and the
report helper can export evaluator rows with `patch`/`prefix` for
`swe_bench_pro_eval.py` compatibility. Tests must cover both names to avoid a
format mismatch.

### Finding 4: Report-only scaffolding must not pretend the paid pilot ran

Status: resolved

Resolution: The replay report is fixture-backed. Live execution commands are
planned but refused unless `--allow-live` and `--max-budget-usd > 0` are both
present. Final reporting must distinguish replay from a live graded pilot.

### Finding 5: A point-estimate lead is not enough

Status: resolved

Resolution: The report declares `clears_noise_floor` only when the lower bound
of the delta CI clears 0.03. Otherwise the verdict is `inconclusive_or_null`,
even if the point estimate is above 3pt.
