# PRD: Complete Supervisor Auto-Improvement Remainder

## Intent

Finish the remaining auto-improvement pieces from the loop graph without weakening supervisor authority. The loop should be able to drain AutoResearch drafts, safely accumulate learning, audit its own evidence quality, and make D1/D2 decisions only from measured trend data.

## Promise Contracts

### P1: Operator can drain AutoResearch intake
- User-visible promise: An operator can activate a small number of draft experiments and park/deny the rest so stale drafts do not block new draft generation.
- Public boundary: `codex-supervisor-axi experiments`.
- Allowed outcomes: `activate` moves `draft` to `runnable`; `park` moves `draft` or `runnable` to `parked` with an audited reason; parked rows are excluded from open draft capacity.
- Forbidden outcomes: Parked experiments run automatically; generic `deny` records an event but leaves queue status unchanged; activation is possible from `parked`.

### P2: Learning accumulation is task-class scoped
- User-visible promise: A policy overlay can target a task class without affecting unrelated task classes.
- Public boundary: gate-start lead instruction composition.
- Allowed outcomes: global overlay blocks still apply; `task_class_overlays.<task_class>` applies only to matching task class and records hashes in the snapshot.
- Forbidden outcomes: A large/source-change overlay changes instructions for unrelated task classes.

### P3: Frozen task classes block policy guidance but not gates
- User-visible promise: Operators can freeze a task class to stop overlay guidance while keeping gates authoritative and runnable.
- Public boundary: gate-start lead instruction composition and overlay snapshot events.
- Allowed outcomes: frozen task class emits a snapshot with `overlay_frozen=true`; no policy guidance block is appended; gates still proceed.
- Forbidden outcomes: freeze advances, denies, or blocks a gate by itself.

### P4: Empty-floor policy proposals are non-applyable
- User-visible promise: A policy proposal derived from AutoResearch cannot become applyable unless it beats an empty/no-overlay floor.
- Public boundary: policy proposal derivation/validation.
- Allowed outcomes: missing or losing empty-floor evidence marks proposal `draft`/`blocked`/`non_applyable`; winning evidence may create the existing human-approved proposal path.
- Forbidden outcomes: accepted report with no empty-floor comparison becomes directly applyable.

### P5: Re-baseline cadence exists and is observational
- User-visible promise: The daemon can schedule empty-floor rebaseline checks for live overlays without mutating policy.
- Public boundary: daemon task / AXI doctor health.
- Allowed outcomes: due overlays emit observational rebaseline events or draft evidence requests; no policy mutation occurs.
- Forbidden outcomes: rebaseline applies rollback or changes overlay by itself.

### P6: D1/D2 remain data-gated
- User-visible promise: CLI promotion and output format default are not declared complete until trends have sufficient samples.
- Public boundary: `codex-supervisor-axi trends`.
- Allowed outcomes: report says "insufficient data" when TOON samples or MCP/AXI denominators are weak.
- Forbidden outcomes: promote CLI/TOON from share-of-total or one-sided sample data.

## Out Of Scope

- Replacing the runtime, adopting Temporal/Restate/DBOS, or changing gate authority.
- Automatically approving or applying policy.
- Removing SQLite support.

