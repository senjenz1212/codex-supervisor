# Repo Commit Hygiene PRD

Task id: `repo-commit-hygiene-20260626`

## Problem Statement

The current worktree mixes an in-limbo no-mistakes safety-gate change, runtime scratch output, lockfile noise, and dual-agent packet updates. Later no-mistakes runs require a clean committed branch, so this slice must land the safety-gate work without bundling scratch or transcript noise into the same commit.

## Solution

Land the no-mistakes source and tests as a focused reviewed commit, ignore `.scratch/` broadly so runtime state stops appearing as untracked work, and keep packet documentation explicit and separate from scratch content. The slice does not touch mergeability, benchmark, or auto-evolution code.

## PRD Promise Contracts

### P1. The no-mistakes change lands reviewed and tested.

Public boundary

`tests/test_no_mistakes.py` must be green for the no-mistakes safety-gate behavior, including `test_no_mistakes_artifact_stash_restore`.

Chosen seam

`run_no_mistakes_validation(...)` in `supervisor/no_mistakes.py`, with subprocess and git effects faked below the public test boundary.

Allowed outcomes

- A self-contained commit containing `supervisor/no_mistakes.py` and `tests/test_no_mistakes.py`.
- Targeted no-mistakes tests pass before commit.
- The commit message is scoped to the no-mistakes safety-gate feature.

Forbidden outcomes

- Bundling `.scratch/*`, transcript replay output, unrelated packet docs, or benchmark/mergeability code into the no-mistakes source commit.
- Treating an exit-zero no-mistakes run without a passing outcome as accepted.
- Losing workflow artifact edits when a temporary stash is used for clean validation.

### P2. Scratch is ignored, not committed.

Public boundary

`.gitignore` plus `git status --porcelain` must show `.scratch/` no longer appears as untracked content.

Chosen seam

The local repository ignore rules under `.gitignore`.

Allowed outcomes

- `.scratch/` is ignored broadly.
- Existing `.scratch/gui-probes/` remains harmless as a narrower redundant ignore.
- Scratch runtime state is left on disk but excluded from commits.

Forbidden outcomes

- Committing `.scratch/*` content.
- Deleting local scratch state as a substitute for ignoring it.
- Leaving `.scratch/*.json`, images, transcripts, or generated repos visible in normal `git status`.

## User Stories

1. As the operator, I want the no-mistakes safety-gate change committed by itself, so that later review can judge that gate without unrelated noise.
2. As the operator, I want scratch output ignored, so that future no-mistakes runs do not block on local runtime debris.
3. As the operator, I want packet docs explicit, so that review evidence is preserved without accidentally committing runtime scratch.

## Implementation Decisions

- Use the existing no-mistakes module and tests; do not introduce a new module.
- Add `.scratch/` to `.gitignore`; do not stage scratch files.
- Keep benchmark and mergeability code out of scope for this slice.

## Testing Decisions

- Run the named artifact-stash restore test before committing.
- Run the focused `tests/test_no_mistakes.py` suite if the named test passes.
- Verify `git status --porcelain` no longer reports `.scratch/`.

## Out of Scope

- SWE-bench Pro oracle work.
- Auto-evolution benchmark-to-policy bridge work.
- Rewriting or broadening no-mistakes beyond the in-limbo safety-gate behavior already present.

## Further Notes

The pre-existing dirty packet docs and lockfile noise must not be folded into the no-mistakes source commit. They should be handled explicitly after the source/test commit is reviewed and tested.
