# TDD Grill Findings

## Status

All findings are resolved in the issue pack and coverage index.

## Findings

### T1. Adapter Tests Must Not Become Helper-Only

Finding: It would be easy to test only `ClaudeCodeAdapter.normalize_hook()`.

Resolution: The first RED test for target support uses
`target_config_load` or `target_adapter_conformance`, then helper tests may cover
normalization details.

### T2. Hook Tests Need Persistence Assertions

Finding: A hook HTTP response test alone would miss the audit promise.

Resolution: Hook issue requires asserting both HTTP response and
`hook_requests` or normalized event persistence.

### T3. Replay Must Use Frozen Inputs

Finding: A replay test that reads current config would not prove the PRD promise.

Resolution: Replay issue requires fixture snapshot input and forbids live config,
Telegram, target agent, or model APIs by default.

### T4. Redaction Must Cover All Storage Surfaces

Finding: Testing only event persistence would miss `actions`, `verdicts`, and
Telegram text.

Resolution: State issue covers `events`, `hook_requests`, `actions`, `verdicts`,
and Telegram-bound formatting.

### T5. Codex Compatibility Should Be a Conformance Test

Finding: Codex future support can drift unless it is represented by a shared
adapter contract.

Resolution: Codex compatibility issue uses the same conformance boundary as
Claude Code with fake rollout and resume fixtures.
