# TDD Grill Findings: EXP-001

This is a retrospective integration trace, not a synthetic skill receipt.

1. Tests freeze deterministic HMAC assignment before any arm starts.
2. Storage tests reject assignment, transition, and terminal-result mutation.
3. Blinding tests cover nested, encoded, free-text, and output-level arm
   leakage.
4. Common pre-treatment failure may rerun the whole block once; a third block
   and post-treatment reruns are rejected.
5. Repository-arm tests enforce clean workspaces, state roots, retry budgets,
   B/C parity, and runtime identity isolation.
