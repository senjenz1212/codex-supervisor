# CS20 — Cold-Start vs Normal-Turn Desktop IPC Spike

## PRD Promise

CS15 — Desktop GUI Reflection Viability Is Evidence-Gated.

Before the supervisor claims Codex Desktop GUI reflection is near-term or
enables a GUI-live transport, run a bounded IPC spike that compares Desktop
cold-start hydration with a normal GUI turn and records a sanitized evidence
branch.

## Public Boundary

`codex_desktop_ipc_capture_diff`

- `summarize_desktop_ipc_capture(path, phase=...)`
- `diff_desktop_ipc_captures(cold_start_path, normal_turn_path)`

## Allowed Outcomes

- Store sanitized IPC frames containing method names, message types,
  client-discovery method names/parameter keys, thread stream change types, and
  patch paths.
- Recommend one next branch:
  - `probe_candidate_live_turn_method`
  - `probe_candidate_forced_reload_method`
  - `prepare_codex_issue`
- Keep `desktop_gui_repaint=unverified` until a human-visible or
  renderer-observed smoke proves otherwise.
- Leave live supervisor config unchanged.

## Forbidden Outcomes

- Store prompt text, patch values, raw params, or secrets from IPC captures.
- Fixture tests connect to the real Desktop IPC socket.
- Fixture tests start or steer Codex turns.
- Live experiments start or steer a Codex turn without explicit HITL.
- Treat `thread/inject_items`, cache invalidation, stream snapshots, or method
  candidates as verified GUI reflection by themselves.
- Continue exploration past the 4-hour CS20 timebox without a fresh decision.

## Spike Run-Of-Show

Timebox: 4 hours.

### Step 0 — Cold-Start Capture

Goal: observe how Desktop constructs in-memory thread state from existing
history.

1. Start the read-only IPC observer.
2. Quit/reopen Codex Desktop or open an existing thread from a cold state.
3. Capture frames to:
   `.scratch/gui-probes/cs20-cold-start-ipc.jsonl`
4. Do not inject, steer, or type during this capture.

Command:

```bash
./.venv/bin/python -m supervisor.desktop_ipc_probe capture \
  --phase cold_start \
  --output .scratch/gui-probes/cs20-cold-start-ipc.jsonl \
  --duration-s 30 \
  --limit 200
```

### Step 1 — Normal-Turn Capture

Goal: observe the renderer-backed write path for a normal user turn.

1. Start a fresh read-only IPC observer.
2. Sam manually sends a harmless prompt in Codex Desktop, preferably in a
   throwaway or explicitly approved session.
3. Capture frames to:
   `.scratch/gui-probes/cs20-normal-turn-ipc.jsonl`

Command:

```bash
./.venv/bin/python -m supervisor.desktop_ipc_probe capture \
  --phase normal_turn \
  --output .scratch/gui-probes/cs20-normal-turn-ipc.jsonl \
  --duration-s 45 \
  --limit 300
```

### Step 2 — Diff

Run:

```bash
./.venv/bin/python -m supervisor.desktop_ipc_probe diff \
  --cold-start .scratch/gui-probes/cs20-cold-start-ipc.jsonl \
  --normal-turn .scratch/gui-probes/cs20-normal-turn-ipc.jsonl \
  --output .scratch/gui-probes/cs20-capture-diff.json
```

Read the result:

```bash
cat .scratch/gui-probes/cs20-capture-diff.json
```

### Step 3 — Branch

- If `candidate_live_turn_methods` is non-empty, design CS21 around the safest
  candidate. Fixture-first; no production config change until live smoke.
- Else if `candidate_forced_reload_methods` is non-empty, design CS21 around
  append-then-reload. Accept visible reload if the smoke proves it.
- Else write the Codex issue with cold-start and normal-turn evidence, then
  stop GUI reflection work and keep Telegram as the live surface.

## TDD Plan

1. RED: replay fixture captures and assert the missing
   `summarize_desktop_ipc_capture` / `diff_desktop_ipc_captures` boundary.
2. GREEN: implement path/method/count-only summaries and diff.
3. RED/GREEN: fixture includes secrets, prompt text, and patch values; assert
   they are absent from serialized summaries and diffs.
4. RED/GREEN: add `supervisor.desktop_ipc_probe capture|summary|diff`; capture
   writes sanitized frames only.
5. Verify: focused CS20 test, existing IPC tests, full suite, compileall.

## Grill Findings

PRD grill: `docs/grill-findings-cs20-desktop-ipc-spike.md`

TDD grill: `docs/grill-findings-tdd-cs20-desktop-ipc-spike.md`

All findings resolved in this issue plan before implementation.

## Implementation Notes

- Added `sanitize_desktop_ipc_message` so live capture files retain only
  allowed protocol structure.
- Added `supervisor.desktop_ipc_probe capture|summary|diff`.
- Capture treats socket receive timeouts as quiet periods, not failures.
- Focused IPC suite and full test suite must stay green before any live spike
  result is treated as evidence.

## Live Read-Only Smoke

Command:

```bash
./.venv/bin/python -m supervisor.desktop_ipc_probe capture \
  --phase normal_turn \
  --output .scratch/gui-probes/cs20-live-readonly-smoke-ipc.jsonl \
  --duration-s 8 \
  --limit 40
```

Result:

- Captured 8 sanitized frames from the real Desktop IPC socket.
- Summary written to
  `.scratch/gui-probes/cs20-live-readonly-smoke-summary.json`.
- Observed methods: `thread-stream-state-changed`.
- Observed client discovery: `ide-context`.
- Observed patch paths: `latestTokenUsageInfo`, `turns/12/items/225`.
- `desktop_gui_repaint` remains `unverified`.

This smoke proves the read-only capture tool works. It is not the full CS20
decision spike because a true cold-start capture would require quitting or
reopening the Codex Desktop app that is hosting this conversation, and a normal
GUI-turn capture needs a manual harmless prompt from Sam in the target Desktop
session.

## Active-Turn Capture

To avoid coordinate GUI automation while still observing live Desktop-rendered
activity, the probe captured IPC while this Codex Desktop turn produced
harmless shell output.

Command:

```bash
./.venv/bin/python -m supervisor.desktop_ipc_probe capture \
  --phase normal_turn \
  --output .scratch/gui-probes/cs20-active-turn-ipc.jsonl \
  --duration-s 20 \
  --limit 120
```

Concurrent harmless activity:

```bash
sleep 2; printf 'cs20-active-turn-harmless-output\n'; date; \
  sleep 2; printf 'cs20-active-turn-harmless-output-done\n'
```

Result:

- Captured 26 sanitized frames.
- Observed methods: `thread-stream-state-changed`.
- Observed client discovery: `ide-context`.
- Observed patch paths:
  - `latestTokenUsageInfo`
  - `turns/12/items/961`
  - `turns/12/items/962`
  - `turns/12/items/963`
  - `turns/12/items/964`
  - `turns/220/items/13`
  - `turns/220/items/14`
  - `turns/80/items/8`
  - `turns/80/items/9`
- No external `turn/start`, `turn/steer`, `thread/read`, `thread/reload`, or
  `thread/hydrate` method appeared in the observer stream.

## Automated Branch Result

Because a true cold-start capture would close the host app, the automated diff
used a snapshot-only baseline from the active-turn capture:

- baseline:
  `.scratch/gui-probes/cs20-snapshot-baseline-ipc.jsonl`
- active turn:
  `.scratch/gui-probes/cs20-active-turn-ipc.jsonl`
- diff:
  `.scratch/gui-probes/cs20-active-turn-diff.json`

Diff result:

```json
{
  "candidate_forced_reload_methods": [],
  "candidate_live_turn_methods": [],
  "methods_only_in_cold_start": [],
  "methods_only_in_normal_turn": [],
  "recommended_next_step": "prepare_codex_issue",
  "desktop_gui_repaint": "unverified"
}
```

Decision: prepare the Codex issue draft now, while keeping the optional true
cold-start capture as the only remaining human-assisted refinement.

Issue draft:
`.scratch/claude-supervisor-telegram/20-codex-external-gui-reflection-issue-draft.md`
