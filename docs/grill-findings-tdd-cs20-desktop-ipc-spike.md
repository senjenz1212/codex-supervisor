# CS20 TDD Grill Findings — Desktop IPC Capture Diff

## Finding 1 — The first RED must hit the spike boundary, not a helper

A helper that classifies one message would not prove the CS20 run can compare
cold-start and normal-turn captures.

Resolution: the first test imports `summarize_desktop_ipc_capture` and
`diff_desktop_ipc_captures`, which are the public boundary for the spike.

## Finding 2 — Fixture tests must not recreate the live mutation risk

The previous `turn/steer` work intentionally kept route probing off the real
Desktop socket. CS20 should preserve that guard.

Resolution: capture diff tests replay JSONL fixtures only. They do not create a
`CodexDesktopIpcClient`, connect to the real socket, start turns, or steer
turns.

## Finding 3 — Sanitization needs adversarial fixture values

It is easy to say "paths only" while accidentally retaining patch values or
request params in a nested summary.

Resolution: fixtures include secret-looking strings, prompt text, and patch
values. Tests assert those strings are absent from the serialized summaries and
diffs.

## Finding 4 — Candidate methods are recommendations, not production behavior

The analyzer can suggest a branch, but it must not enable Desktop GUI transport
or classify repaint as verified.

Resolution: analyzer output includes `recommended_next_step` and always reports
`desktop_gui_repaint=unverified`.
