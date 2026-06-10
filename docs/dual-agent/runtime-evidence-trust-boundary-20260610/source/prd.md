# Runtime Evidence Trust Boundary PRD

## Problem Statement

Slice 1 made execution and outcome gates depend on supervisor-generated runtime evidence, but two boundary gaps remain. Caller-supplied receipts can still arrive with trusted-looking provenance strings, and declared test commands are still broad command text. A capable or confused agent could stamp `source="supervisor"` or `evidence_grade="runtime_native"` on self-reported evidence, or declare a shell-like test command that does more than run tests. The supervisor needs a boundary that treats provenance as process-owned state, not user-supplied text.

## Solution

Sanitize all caller-supplied receipt provenance before gate checks, preserve supervisor-grade trust only for receipts produced by the in-process runtime evidence collector during the current gate invocation, and execute declared tests through a narrow argv allowlist. Runtime evidence collection will run pytest-style commands in an isolated copy with a scrubbed environment. Rejected commands and unavailable test environments become red runtime evidence receipts rather than silent degradation.

## User Stories

- As an operator, I want forged supervisor/runtime-native receipts downgraded before they can satisfy execution or outcome gates.
- As a reviewer, I want claim verification to trust the runtime evidence collector for the current gate, not any receipt with matching strings.
- As a maintainer, I want declared tests rerun safely without inherited API keys, arbitrary shell expansion, or `python -c` execution.
- As a release owner, I want an unavailable validation environment to fail loudly so accepted gates never rest on existence checks alone.

## PRD Promise Contracts

P1. Caller provenance cannot forge supervisor evidence.
Public boundary: `run_dual_agent_workflow`, `submit_dual_agent_workflow_job`, `verify_workflow_claims`, and `verify_gate_deliverable_evidence`.
Allowed outcome: caller receipts claiming supervisor or runtime-native provenance are downgraded to caller/self-reported evidence and ledger events record the downgrade.
Forbidden outcome: a stamped caller receipt satisfies implementation, changed-file, or tests-passed evidence floors.

P2. In-process runtime evidence remains authoritative for honest runs.
Public boundary: accepted `execution` and `outcome_review` gates.
Allowed outcome: receipts from `collect_runtime_evidence` keep supervisor/runtime-native provenance when their ids match the current gate invocation.
Forbidden outcome: honest runtime evidence is downgraded, ignored, or replaced by stale receipts from a different gate.

P3. Declared tests run through a confined argv allowlist.
Public boundary: `supervisor.runtime_evidence.collect_runtime_evidence`.
Allowed outcome: pytest and narrow make-test patterns run as argv with `shell=False`; non-allowlisted commands produce `runtime_test_command_rejected`.
Forbidden outcome: `python -c`, shell metacharacters, npm scripts, or arbitrary command prefixes execute.

P4. Validation subprocesses receive a scrubbed environment.
Public boundary: runtime evidence test execution.
Allowed outcome: PATH is minimal, the explicit validation interpreter is used, and secret-like env vars are absent.
Forbidden outcome: provider API keys, tokens, credentials, or unrelated process environment leak into the validation subprocess by default.

P5. Test environment failures are hard failures.
Public boundary: runtime evidence receipts and P11 runtime evidence probe.
Allowed outcome: missing pytest or missing executable is reported as `runtime_test_environment_unavailable` and blocks acceptance.
Forbidden outcome: the gate silently skips test execution while keeping a passed test claim.

## Implementation Decisions

- Add a shared receipt provenance helper that marks in-process runtime evidence and downgrades caller-stamped supervisor/runtime-native claims.
- Pass current runtime receipt ids from `collect_runtime_evidence` into claim and deliverable verification instead of trusting receipt fields alone.
- Replace shell execution with argv execution and parse only pytest or narrow make-test declarations.
- Build a minimal validation environment that keeps the validation interpreter usable while dropping secret-like variables.
- Keep gate ordering, reviewer panel behavior, fan-out defaults, and Slice 1 accept semantics unchanged for honest runs.

## Testing Decisions

- Add boundary tests for forged supervisor/runtime-native receipts at workflow and helper surfaces.
- Add runtime evidence tests proving `python -c` is rejected, pytest executes, failing pytest fails, secrets are scrubbed, and missing pytest is red.
- Retain the honest execution gate acceptance regression so the runtime-native floor continues to pass when the supervisor creates receipts in process.
- Run focused runtime evidence and workflow driver tests before the full suite.

## Out of Scope

- No Postgres, network namespace, container sandbox, or new infrastructure in this slice.
- No change to fan-out, reviewer quorum, admission control, or policy defaults.
- No broad redesign of evidence attempts, validator daemons, or AutoResearch policy mutation.
