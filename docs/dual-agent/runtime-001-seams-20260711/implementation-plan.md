# Implementation Plan: RUNTIME-001

## Scope Note

Planning only. This documentation task does not edit runtime source/tests.

## Sequence

1. Keep lifecycle data types and `AgentRuntime` in a provider-neutral module.
2. Put command construction, SDK loading, and raw-event parsing behind runtime
   transports.
3. Normalize lifecycle events and hash the complete result envelope.
4. Keep `ModelClient` small: typed request, typed response, and validated
   structured completion.
5. Migrate call sites one public behavior at a time:
   - `AgentInvoker`
   - drift detector/adjudicator
   - hook critic
   - Telegram supervisor
   - agentic executor
   - Cursor/LiteLLM reviewer paths
   - planning and recovery model calls
6. Move provider construction to composition roots and inject seams.
7. Add a source-scan allowlist limited to provider adapter modules.
8. Run contract tests, process-group cancellation tests, then optional live
   smoke tests with explicitly authorized credentials/budget.

## Stop Conditions

- Do not extend `TargetAgentAdapter`.
- Do not report live cross-runtime parity from fake transports.
- Do not mark the migration complete while direct provider imports remain in
  core modules.
