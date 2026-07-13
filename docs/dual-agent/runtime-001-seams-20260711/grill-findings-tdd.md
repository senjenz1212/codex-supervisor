# TDD Grill Findings: RUNTIME-001

This is a retrospective integration trace, not a synthetic skill receipt.

1. Contract tests execute both runtime classes against one transport and
   compare the normalized schema.
2. Cancellation tests include process groups, detached descendants, timeout,
   and SIGTERM-spawned children.
3. Source scanning rejects provider SDK imports in experiment and evidence
   core modules.
4. Structured model output fails closed on invalid schema.
5. Fake transports prove contracts only; live provider smokes remain separately
   reported evidence.
