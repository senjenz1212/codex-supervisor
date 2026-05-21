# Grill Findings — Desktop GUI Repaint Truth

## G1 — History append is not GUI visibility

Finding: The live failure proves that `thread/inject_items` can append to
rollout/thread history while the already-open Desktop renderer remains stale.

Resolution: CS14 introduces an explicit visibility classifier. Unverified GUI
repaint maps to `history_only`.

## G2 — `delivered` is overloaded

Finding: `delivered=true` means the adapter completed its write. It does not
mean the user's Desktop app rendered the item.

Resolution: Keep adapter delivery as the transport outcome, and add
`visibility.effective_state` as the operator-facing outcome.

## G3 — Do not turn progress mirroring into steering

Finding: A tempting workaround would be to use `codex exec resume` so the GUI
has a "real turn" to show. That starts an agent turn and changes behavior.

Resolution: CS14 keeps passive status as app-server-only and forbids using
resume/prompt injection for visibility.
