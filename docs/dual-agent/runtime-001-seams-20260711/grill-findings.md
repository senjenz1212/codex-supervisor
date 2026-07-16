# PRD Grill Findings: RUNTIME-001

This is a retrospective integration trace, not a synthetic skill receipt.

1. **Accepted:** `AgentRuntime` must remain separate from
   `TargetAgentAdapter`.
2. **Accepted:** resolved model, usage provenance, terminal status, and result
   hashes belong in the provider-neutral result.
3. **Accepted:** experiment-core modules cannot import provider SDKs.
4. **Accepted:** SDK and CLI details may remain in explicit provider
   adapters/transports and composition roots.
5. **Residual gap:** installed CLI presence is not evidence that authenticated
   Claude Code and Codex runs have equivalent live behavior.
