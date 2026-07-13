# Issues: TASK-001

## TK1: Pinned task and frozen result contracts

- PRD promises: P1, P3
- Public boundary: `TaskSpec`, `FrozenTaskResult`
- Blocked by: RUNTIME-001 result hash contract
- Acceptance:
  - Missing required pins fail before materialization.
  - Frozen results content-address the task spec, run result, patch, and output.
  - Patch collection includes tracked, deleted, untracked text, and binary
    files without changing the real Git index.

## TK2: Generic and Unity environments

- PRD promises: P2, P5
- Public boundary: `TaskEnvironmentAdapter`
- Blocked by: TK1
- Acceptance:
  - Both families materialize pinned detached repositories.
  - Unity materialization rejects non-Unity repositories.
  - Reset and teardown leave no cross-arm state.

## TK3: Hidden verifier isolation

- PRD promises: P4
- Public boundary: `VerifierAdapter.verify`
- Blocked by: TK1, TK2
- Acceptance:
  - Hidden roots never enter the public workspace or frozen result.
  - Verification begins only after the result hash is fixed.
  - Leak checks cover paths, metadata, environment, archives, and symlinks.

## TK4: Official verifier adapters

- PRD promises: P5
- Public boundary: `SweBenchVerifier`, `UnityTestFrameworkVerifier`
- Blocked by: TK3
- Acceptance:
  - SWE-bench delegates to the unforked official harness adapter.
  - Unity delegates to a pinned Unity Test Framework runner.
  - Both produce the same grade schema and classify infra/flake failures.

## TK5: Enforce execution policy

- PRD promises: P1, P2
- Public boundary: task materialization/execution composition
- Blocked by: RUNTIME-001, TK2
- Acceptance:
  - Image, architecture, OS, network, and resource pins are enforced, not just
    recorded.
