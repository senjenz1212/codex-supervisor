# PRD Grill Findings: REPLAY-001

This is a retrospective integration trace, not a synthetic skill receipt.

1. **Accepted:** missing schema metadata must fail closed; compatibility cannot
   be inferred from a current default.
2. **Accepted:** regrade, replay, and rerun are different operations and must
   retain distinct provenance.
3. **Accepted:** historical verification must use a recorded immutable
   checkout or snapshot, never the live worktree.
4. **Accepted:** a model alias is not an exact served-model pin.
5. **Residual gap:** local tests prove snapshot isolation, not universal
   reproducibility of every historical external dependency.
