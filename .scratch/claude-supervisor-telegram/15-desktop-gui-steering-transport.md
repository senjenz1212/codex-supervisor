# CS15 — Desktop GUI Steering Transport

## Product Promise

When Sam approves a steering request for a Codex Desktop session, the
supervisor can deliver that steer through the visible Codex Desktop GUI so the
chat itself reflects what happened.

This is a steering transport, not passive progress mirroring. It starts a real
Codex turn and therefore must remain approval-gated. Passive watched-run
progress remains Telegram-first, with app-server history append classified as
`history_only` until a non-turn GUI repaint API is verified.

## Public Boundary

`codex_desktop_gui_steering`

Submit `TargetAction(kind="inject_steering")` through `CodexAdapter` with target
config `steering_delivery: desktop_gui`.

## Allowed Outcomes

- With explicit desktop GUI delivery configured and a verifier-backed GUI
  controller, the adapter may return `delivered=true`, `method=desktop_gui_submit`,
  `desktop_gui_repaint=verified`.
- The built-in coordinate-only controller may post OS events, but it must not
  claim delivery or GUI repaint without a target-session verification signal.
- Without explicit desktop GUI delivery configured, existing `codex exec resume`
  delivery remains the default.
- If GUI submission fails, the action fails closed and does not silently fall
  back to invisible resume delivery.
- The message is redacted before reaching the clipboard or GUI helper.
- The GUI transport first selects the configured target chat row before
  submitting; missing session-selection coordinates fail closed.

## Forbidden Outcomes

- Passive progress status uses GUI submission and starts Codex turns.
- GUI steering runs without the existing Telegram approval path.
- GUI steering silently falls back to app-server history append or CLI resume
  after a GUI failure.
- Raw secrets reach clipboard, process arguments, action rows, or screenshots.

## TDD Plan

1. RED: `CodexAdapter.execute_action(TargetAction(kind="inject_steering"))`
   with `steering_delivery=desktop_gui` calls a GUI controller, not CLI resume,
   and returns GUI-live metadata.
2. RED: GUI delivery redacts secrets before controller submission.
3. RED: missing GUI coordinates fail closed before subprocess/clipboard work.
4. RED: missing target-session selection coordinates fail closed, because the
   active Desktop chat may be a different conversation.
5. GREEN: add `CodexDesktopGuiController` and adapter routing.
6. Verify: run unit suite and perform one live smoke in the visible Vela chat.
   If the smoke cannot prove target-session delivery, keep live config on
   `steering_delivery: resume`.
