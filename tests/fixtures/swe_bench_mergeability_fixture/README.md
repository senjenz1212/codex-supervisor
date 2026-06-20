# SWE-bench mergeability fixture

Public boundary owner: `swebench_mergeability_fixture_runner` in
`supervisor/swe_bench_mergeability.py`.

This fixture exists to exercise the fixture-first executable mergeability
runner without depending on live SWE-bench, network fetches, or live solvers.
Tests under `tests/test_swe_bench_pro_mergeability_bridge.py` build small
synthetic fixture trees on the fly (writing public source files plus protected
oracle files) and pass them into the runner.

Layout convention exercised by the runner and its tests:

```
<fixture_root>/
    parser.py            # public source files that may be modified by candidates
    src/...              # additional public source files
    hidden/              # protected oracle files - excluded from the public worktree
        test_behavior.py # hidden test behaviour the public probe must not see
    .mergeability/       # protected oracle directory - excluded from the public worktree
        oracle.json      # hidden oracle accept labels and metadata
```

The runner copies only files outside the configured `protected_paths` into a
fresh public worktree, applies the candidate `patch_operations`, runs the
configured public commands, builds a public-only reviewer packet, freezes
baseline / S_probe / S_full decisions, and only then runs the deterministic
local oracle commands. The default `protected_paths` mirror the mergeability
bench (`hidden/`, `.mergeability/`) plus any SWE-bench Pro fixture extensions.

This directory is intentionally minimal because the runner accepts the fixture
root as an input argument; tests construct the actual file layouts in their
own temp directories. The README is kept here so the public-boundary contract
is discoverable from the fixture path used by tests.
