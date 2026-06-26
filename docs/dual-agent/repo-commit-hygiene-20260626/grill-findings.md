# Grill Findings

## Gate: PRD Review

Status: resolved

### Finding G1: `.scratch/` was misclassified as ignored.

Evidence: `.gitignore` previously ignored only `.scratch/gui-probes/`, so normal `git status --short` exposed many `.scratch/` files.

Resolution: P2 requires a broad `.scratch/` ignore and forbids committing scratch content.

### Finding G2: The no-mistakes commit can be contaminated by packet or transcript output.

Evidence: `git status --short` showed `supervisor/no_mistakes.py`, `tests/test_no_mistakes.py`, `uv.lock`, existing packet docs, untracked packet directories, and scratch output at the same time.

Resolution: P1 requires the source/test commit to be self-contained. Packet docs and lockfile noise must be handled separately.

### Finding G3: The requested first RED test name must be stable.

Evidence: The supervisor intent names `tests/test_no_mistakes.py::test_no_mistakes_artifact_stash_restore`.

Resolution: The artifact-stash behavior test is named exactly as the supervisor intent requires.
