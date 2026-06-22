## Findings

1. The original plan could overclaim Cursor SDK sandboxing as a read-only boundary. Resolution: require physical isolated-worktree execution and treat `sandbox_options.enabled=True` only as defense in depth.
2. The original mutation guard detected drift after Cursor had already shared the source worktree. Resolution: move Cursor SDK execution into a copied disposable worktree while keeping the source `git status` guard.
3. The full-panel evidence promise spans Cursor invocation and mergeability aggregation. Resolution: keep `CursorInvocationResult` as the public seam so missing or unsafe Cursor verdicts remain unavailable through the existing reviewer registry.
4. Oracle isolation must cover the copied reviewer worktree, not just the prompt packet. Resolution: exclude hidden oracle and protected benchmark artifacts from the disposable copy and test that exclusion.

All findings are resolved in the PRD and TDD plan. No domain glossary update is needed because existing terms such as S_full, reviewer panel, and Codex-only calibration are already established in nearby mergeability artifacts.
