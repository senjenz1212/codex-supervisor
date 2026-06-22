## Slice 1: Isolate Cursor Reviewer Execution

PRD promise: P1, P2, P5

Public boundary: `invoke_cursor_agent`

Chosen seam: Cursor reviewer invocation request interface.

Representative action: A Cursor SDK reviewer writes a file while reviewing a gate.

Allowed outcomes: The write lands only inside a disposable reviewer worktree, the original source worktree remains unchanged, and diagnostics record the contained mutation.

Forbidden outcomes: Cursor writes to `supervisor/`, `tests/`, `scripts/`, `config/`, or any hidden oracle artifact in the source worktree.

Acceptance criteria:
- [ ] Cursor SDK review uses an isolated reviewer worktree by default.
- [ ] Source worktree before and after status remains unchanged when Cursor writes inside the reviewer worktree.
- [ ] Contained isolated worktree mutations are recorded in diagnostics.
- [ ] Hidden oracle and protected benchmark artifact names are excluded from the reviewer worktree.

## Slice 2: Keep Unsafe Cursor Mutations Blocking

PRD promise: P1, P3

Public boundary: `invoke_cursor_agent`

Chosen seam: Existing source-worktree mutation guard.

Representative action: A non-isolated Cursor path writes to the original worktree.

Allowed outcomes: The result is red with `cursor_modified_worktree`, has no accepted verdict, and cannot feed full-panel evidence.

Forbidden outcomes: The reviewer accepts or returns usable full-panel evidence after mutating source, test, script, or config files.

Acceptance criteria:
- [ ] Non-isolated source mutations still block.
- [ ] The blocking diagnostic includes before and after status.
- [ ] Reviewer registry sees unsafe Cursor results as missing or unavailable evidence.

## Slice 3: Preserve Full-Panel and Calibration Labels

PRD promise: P3, P4

Public boundary: configured mergeability reviewer panel and paired acceptance pilot report.

Chosen seam: `build_configured_reviewer_panel` and existing reviewer aggregation.

Representative action: Full-panel evaluation receives a safe Cursor verdict plus a safe Codex verdict, then receives a missing Cursor verdict.

Allowed outcomes: Full roster verdicts can make S_full available; missing or unsafe Cursor verdicts keep S_full unavailable. Codex-only calibration remains separately labeled.

Forbidden outcomes: Codex-only fallback populates full-panel S_full evidence or missing Cursor evidence is treated as acceptance.

Acceptance criteria:
- [ ] S_full availability requires safe Cursor and Codex verdicts.
- [ ] Missing Cursor verdict keeps full-panel evidence unavailable.
- [ ] Codex-only fallback remains calibration-only and non-applyable.
