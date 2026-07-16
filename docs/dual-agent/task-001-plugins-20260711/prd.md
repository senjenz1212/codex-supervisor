# PRD: TASK-001 Task and Verifier Plugins

## Problem

An efficacy experiment cannot be trusted if task setup, patch collection, and
hidden verification share a workspace or provider-specific schema. Task inputs
must be pinned, and hidden tests must remain inaccessible until the agent result
is frozen.

## Goal

Provide separate `TaskEnvironmentAdapter` and `VerifierAdapter` seams with
generic repository and Unity task families, frozen result envelopes, and
official-verifier adapters.

## Promise Contracts

### P1: Task specifications are pinned

- Public boundary: `TaskSpec`.
- Pins include repository/revision, dataset/split hashes, public statement,
  image digest, architecture/OS, network policy, resource limits, and verifier
  identity/hash.

### P2: Materialization is isolated and repeatable

- Public boundary: `TaskEnvironmentAdapter.materialize/reset/teardown`.
- Each arm receives a detached checkout; reset removes tracked and untracked
  changes.

### P3: Result freeze is complete

- Public boundary: `collect_patch` and `FrozenTaskResult`.
- Tracked, deleted, untracked text, and untracked binary changes produce one
  applyable binary patch without mutating the agent's index.

### P4: Hidden verification is isolated

- Public boundary: `VerifierAdapter.verify`.
- The verifier receives only a frozen result; hidden material is absent from
  the agent workspace and frozen envelope.

### P5: Task families share schemas

- Generic and Unity tasks return identical materialized/frozen shapes.
- `SweBenchVerifier` delegates to the official oracle adapter without changing
  pass/fail semantics.
- `UnityTestFrameworkVerifier` runs only after freeze and returns the common
  grade schema.

## Non-goals

- Proving a live Unity Editor or SWE-bench container run in unit tests.
- Enforcing network/container resource policy solely by recording fields.
- Bundling environment and verifier into one interface.
