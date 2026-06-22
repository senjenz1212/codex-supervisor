## Public-Boundary RED 1: Cursor Writes Are Contained

RED: Through `invoke_cursor_agent`, use a fake Cursor SDK runner that writes a file into `request.cwd` and returns a valid typed outcome. The test should fail until Cursor SDK attempts run in a disposable reviewer worktree rather than the original git worktree.

GREEN: Add isolated Cursor reviewer worktree support, pass the isolated cwd to the SDK runner, and keep checking the original source worktree before and after review.

## Public-Boundary RED 2: Contained Mutation Diagnostics Are Preserved

RED: Through `invoke_cursor_agent`, make the fake Cursor runner write inside the isolated cwd and assert result diagnostics include isolation strategy, source cwd, isolated cwd, contained mutation status, and changed paths or snapshot hashes.

GREEN: Snapshot the isolated reviewer worktree before and after Cursor execution and attach a compact diagnostic payload to reviewer metadata.

## Public-Boundary RED 3: Unsafe Source Mutation Still Blocks

RED: Through `invoke_cursor_agent` with isolation disabled, make the fake Cursor runner write to the original git worktree and assert the result is red with `cursor_modified_worktree`.

GREEN: Preserve the existing source status guard and ensure isolation changes do not weaken non-isolated safety behavior.

## Adapter RED 4: Cursor SDK Sandbox Is Passed But Not Trusted

RED: Monkeypatch the Cursor SDK modules and assert `_run_cursor_sdk` passes `sandbox_options` with `enabled=True` into `LocalAgentOptions`.

GREEN: Build the local Cursor options with sandbox options when the installed SDK exposes them, while keeping worktree isolation as the actual source safety mechanism.

## Public-Boundary RED 5: Hidden Oracle Artifacts Are Excluded

RED: Through `invoke_cursor_agent`, create source files that look like hidden oracle or protected benchmark artifacts and assert the fake Cursor runner cannot see them in the isolated cwd.

GREEN: Add a reviewer-worktree ignore policy that excludes `.mergeability`, hidden oracle artifacts, oracle output files, `FAIL_TO_PASS`, `PASS_TO_PASS`, and heavyweight local runtime directories.

## Integration RED 6: Full-Panel Evidence Requires Safe Cursor Verdict

RED: Drive the configured mergeability panel with one valid Codex verdict and one unsafe or missing Cursor result. Assert S_full remains unavailable. Also verify a full safe roster can be available and Codex-only calibration remains non-full-panel.

GREEN: Reuse the existing reviewer registry behavior and only adjust missing diagnostics if the isolation result is not already represented as unavailable evidence.

## Required Test Commands

`.venv/bin/python -m pytest tests/test_cursor_agent.py`

`.venv/bin/python -m pytest tests/test_mergeability_bench.py`
