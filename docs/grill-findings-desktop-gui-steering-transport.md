# Grill Findings — Desktop GUI Steering Transport

## G1 — GUI delivery must not become passive progress streaming

Finding: The live smoke proves GUI submission works by starting a real Codex
turn. Using it for every watched-run progress update would create feedback
loops and mutate the target session.

Resolution: CS15 limits GUI delivery to `inject_steering`, which already flows
through explicit Telegram approval. `append_status_item` remains the passive
history-only path.

## G2 — GUI failure must not silently downgrade to invisible resume

Finding: The original operator pain was a steer reported as delivered while the
Desktop GUI did not reflect it. Falling back from GUI delivery to CLI resume
would recreate that ambiguity.

Resolution: GUI delivery returns `failed` on GUI failure. Operators may retry
with a different configured transport explicitly.

## G3 — Coordinates are host-specific operational config

Finding: The verified input click point depends on the user's monitor layout
and Codex window placement.

Resolution: The adapter requires explicit configured coordinates for
`desktop_gui` delivery and fails closed when absent. This avoids guessing at
arbitrary screen positions.

## G4 — Active chat must be selected before submit

Finding: A live approval-path smoke showed that GUI submission can land in the
wrong Codex chat if the Desktop is currently focused on another conversation.

Resolution: The GUI transport now requires a configured target-chat click point
and selects it before clicking the input box. Missing session-selection
coordinates fail closed.

## G5 — Input focus is more fragile than row selection

Finding: After selecting the target chat, the first host input coordinate did
not focus the composer, even though earlier direct Vela-only tests had worked.

Resolution: The coordinate-only controller no longer claims successful delivery
or verified GUI repaint. Live config was reverted to `steering_delivery: resume`
until a session-bound GUI API or verifier-backed controller exists.
