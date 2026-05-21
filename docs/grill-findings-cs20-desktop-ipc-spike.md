# CS20 PRD Grill Findings — Desktop IPC Reflection Spike

## Finding 1 — The spike must produce a decision, not another open-ended probe

The product promise is not "make the GUI reflect somehow." It is "spend a
bounded amount of time finding whether a Desktop-owned method exists, then
commit to one of three branches."

Resolution: CS15 names the accepted branches: existing live-turn method,
forced-reload method, or external Codex handler request. The CS20 issue has a
4-hour timebox and a halt condition.

## Finding 2 — Cold-start hydration is a distinct product question

Normal-turn capture can reveal the write path, but it may miss the only path
that loads rollout history into Desktop's in-memory model.

Resolution: CS20 step 0 is a cold-start capture before the normal-turn capture.
The diff explicitly surfaces candidate forced-reload methods from cold-start
only traffic.

## Finding 3 — Evidence must stay sanitized

IPC captures can contain prompts, patch values, workspace paths, and secrets.
The supervisor needs protocol structure, not raw content.

Resolution: the public boundary stores message types, method names, discovery
method names, stream change types, and patch paths only. Tests include secrets,
prompt text, and patch values and assert they do not appear in summaries.

## Finding 4 — GUI reflection remains a separate proof

A method candidate or stream patch is not itself proof that Sam's visible
Desktop window repainted.

Resolution: CS15 keeps `desktop_gui_repaint=unverified` until an independent
human-visible or renderer-observed smoke test proves the visible update.
