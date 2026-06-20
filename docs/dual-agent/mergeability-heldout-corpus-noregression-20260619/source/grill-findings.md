### Finding 1: No-regression must not protect false accepts

Status: resolved

The previous implementation marked baseline-accepted oracle-negative rows as no-regression failures. This contradicts the slice requirement because prior false accepts are exactly what the stricter gate should be allowed to reject.

### Finding 2: Per-task controls are stricter than per-class controls

Status: resolved

The prior coverage check validated controls by task class, but this slice requires every task to carry a positive control, negative control, and public-pass hidden-fail trap.

### Finding 3: Held-out reporting needs explicit uncertainty

Status: resolved

Existing arm summaries exposed counts and rates, but not the requested `n_bad`, `n_good`, or labeled interval fields.

### Finding 4: Peak reporting must be structurally disallowed

Status: resolved

The paired report identifies the split used for metrics and explicitly records that best-of-K in-sample peaks are not held-out improvement evidence.
