"""Deterministic replay harness (ticket 05, PRD promise P7).

Replay runs supervisor decisions against *fixture* inputs — snapshot, normalized
events, model-output fixtures — and emits a structured result that can be
diff'd against `expected.json`. Replay must NEVER call live Telegram, target
adapters, subprocess, Anthropic, or OpenAI. Modes and scope come from the
snapshot, not from the running daemon's config.

Replay now composes the deterministic drift pieces: L1 scope findings,
mode-policy proposed actions, and fixture-backed L2/L3/L4 verdicts. The
model-output fixture is required so replay never silently falls back to live
model APIs.

File formats:

  snapshot.json   — one object with keys: run_id, session_id, target, task,
                    scope_contract, modes, codex_cli_version, created_at
  events.jsonl    — one normalized event per line, each a JSON object with at
                    least {id, ts, source, kind, ...} fields
  model_outputs.json — fixture for model outputs that would be invoked during
                       a live run. Schema: {"by_event_id": {}, "by_phase": {}}.
                       Empty objects are valid when a fixture only exercises
                       deterministic L1 behavior.

Run as a CLI:
  python -m supervisor.replay snapshot.json events.jsonl model_outputs.json
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any


def _load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_events(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _load_model_outputs(path: Path | None) -> dict[str, Any]:
    """Load the model-output fixture. If `path` is None or missing, the caller
    decides how to fail-closed (cycle 2 wires this)."""
    if path is None or not Path(path).exists():
        raise FileNotFoundError(
            f"model_outputs fixture not found at {path!r}. "
            "Replay refuses to silently fall back to a live LLM API."
        )
    raw = json.loads(Path(path).read_text())
    raw.setdefault("by_event_id", {})
    raw.setdefault("by_phase", {})
    return raw


_SEVERITY_RANK = {"info": 0, "warn": 1, "severe": 2}
_VALID_PLAN_STATUSES = {"on_plan", "adjacent", "blocked", "exploratory", "abandoned"}
_INTENT_SUMMARY_KINDS = {"intent_summary", "item.intent_summary", "message.intent_summary"}


def _verdicts_from_l1(
    findings: list[dict],
    *,
    run_id: str,
) -> list[dict]:
    """Convert L1 scope findings into a single L1 verdict with the full schema.

    Pure function: same findings always produce the same verdict. This is the
    deterministic slice of what a live DriftDetector L4 verdict would look like;
    L3/L4 model-based fields are not populated here but the schema is compatible.
    """
    if not findings:
        return []

    evidence_ids = [f["event_id"] for f in findings if f.get("event_id") is not None]
    classifications = {f.get("classification", "") for f in findings}
    max_sev = max(
        (f.get("severity", "info") for f in findings),
        key=lambda s: _SEVERITY_RANK.get(s, 0),
    )

    if "protected" in classifications or "never_touch" in classifications:
        recommended_action = "ask_user"
    elif "out_of_scope" in classifications:
        recommended_action = "deny_next_write"
    else:
        recommended_action = "none"

    cls_counts: dict[str, int] = {}
    for f in findings:
        c = f.get("classification", "unknown")
        cls_counts[c] = cls_counts.get(c, 0) + 1
    parts = [f"{n} {c.replace('_', '-')}" for c, n in cls_counts.items()]
    summary = f"L1 scope: {', '.join(parts)}"

    return [{
        "layer":               "L1",
        "run_id":              run_id,
        "classification":      "scope_violation",
        "confidence":          1.0,
        "evidence_event_ids":  evidence_ids,
        "recommended_action":  recommended_action,
        "user_visible_summary": summary,
    }]


def _intent_summary_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("kind") in _INTENT_SUMMARY_KINDS]


def _verdicts_from_model_outputs(
    model_outputs: dict[str, Any],
    *,
    run_id: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert fixture model outputs into replay verdicts.

    This is intentionally fixture-only. It proves the L2/L3/L4 shape without
    calling embeddings, chat models, target adapters, or Telegram.
    """
    by_phase = model_outputs.get("by_phase", {})
    verdicts: list[dict[str, Any]] = []

    l2 = by_phase.get("L2")
    if l2 is not None:
        intent_events = _intent_summary_events(events)
        verdicts.append({
            "layer": "L2",
            "run_id": run_id,
            "classification": "intent_similarity",
            "similarity": l2.get("similarity"),
            "threshold": l2.get("threshold"),
            "intent_summary_event_ids": [
                e["id"] for e in intent_events if e.get("id") is not None
            ],
        })

    l3 = by_phase.get("L3")
    if l3 is not None:
        status = l3.get("plan_status")
        if status not in _VALID_PLAN_STATUSES:
            raise ValueError(
                f"invalid L3 plan_status {status!r}; expected one of "
                f"{sorted(_VALID_PLAN_STATUSES)}"
            )
        verdicts.append({
            "layer": "L3",
            "run_id": run_id,
            "current_step": l3.get("current_step", ""),
            "plan_status": status,
            "rationale": l3.get("rationale", ""),
        })

    l4 = by_phase.get("L4")
    if l4 is not None:
        required = {
            "classification",
            "confidence",
            "evidence_event_ids",
            "recommended_action",
            "user_visible_summary",
        }
        missing = sorted(required - set(l4))
        if missing:
            raise ValueError(f"L4 fixture missing required fields: {missing}")
        verdicts.append({
            "layer": "L4",
            "run_id": run_id,
            "classification": l4["classification"],
            "confidence": l4["confidence"],
            "evidence_event_ids": l4["evidence_event_ids"],
            "recommended_action": l4["recommended_action"],
            "user_visible_summary": l4["user_visible_summary"],
        })

    return verdicts


def replay_from_fixtures(
    *,
    snapshot_path: str | Path,
    events_path: str | Path,
    model_outputs_path: str | Path,
) -> dict[str, Any]:
    """Replay a fixture run. Returns the canonical result dict.

    Output shape (v1):
      {
        "run_id":          str,
        "target":          str,
        "event_count":     int,
        "modes":           dict,    # frozen from snapshot
        "scope_findings":  list,    # filled by ticket 04
        "verdicts":        list,    # filled by ticket 04
        "proposed_actions": list,   # filled by ticket 04
      }
    """
    snapshot = _load_snapshot(Path(snapshot_path))
    events = _load_events(Path(events_path))
    model_outputs = _load_model_outputs(Path(model_outputs_path))

    # Drift L1 — pure scope-policy evaluation. Ticket 04 cycle 1.
    from .scope_policy import evaluate_scope
    scope_findings = evaluate_scope(
        snapshot.get("scope_contract", {}),
        events,
    )

    modes = dict(snapshot.get("modes", {}))

    # Drift mode policy — pure mapping from findings to proposed actions.
    # Ticket 04 cycle 2. Output is action-executor-shaped; ticket 06 will
    # consume this same list to produce durable actions + Telegram delivery.
    from .mode_policy import propose_actions
    proposed_actions = propose_actions(
        scope_findings,
        modes,
        run_id=snapshot["run_id"],
        session_id=snapshot.get("session_id", ""),
    )

    verdicts = _verdicts_from_l1(
        scope_findings,
        run_id=snapshot["run_id"],
    )
    verdicts.extend(_verdicts_from_model_outputs(
        model_outputs,
        run_id=snapshot["run_id"],
        events=events,
    ))

    return {
        "run_id":          snapshot["run_id"],
        "target":          snapshot.get("target", "unknown"),
        "event_count":     len(events),
        # P7: modes come from the snapshot, not from live config.
        "modes":           modes,
        "scope_findings":  scope_findings,
        "verdicts":        verdicts,
        "proposed_actions": proposed_actions,
    }


# ---- CLI ----

def _main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m supervisor.replay "
              "<snapshot.json> <events.jsonl> <model_outputs.json>",
              file=sys.stderr)
        return 2
    result = replay_from_fixtures(
        snapshot_path=argv[0],
        events_path=argv[1],
        model_outputs_path=argv[2],
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
