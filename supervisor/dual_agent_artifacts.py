"""Markdown artifacts derived from dual-agent gate ledger events."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state import State


@dataclass(frozen=True)
class DualAgentArtifactExport:
    status: str
    output_dir: Path
    files: tuple[Path, ...]


def export_dual_agent_run_artifacts(
    state: State,
    *,
    run_id: str,
    task_id: str,
    output_dir: str | Path,
) -> DualAgentArtifactExport:
    events = _read_task_events(state, run_id=run_id, task_id=task_id)
    out_dir = Path(output_dir)
    if not events:
        return DualAgentArtifactExport(status="not_found", output_dir=out_dir, files=())

    out_dir.mkdir(parents=True, exist_ok=True)
    files = (
        out_dir / "index.md",
        out_dir / "prd.md",
        out_dir / "tdd.md",
        out_dir / "grill-findings.md",
        out_dir / "issues.md",
        out_dir / "outcome-review.md",
        out_dir / "transcript.md",
    )
    by_gate = _events_by_gate(events)
    files[0].write_text(_index_markdown(run_id, task_id, by_gate), encoding="utf-8")
    files[1].write_text(_gate_markdown("PRD Gate", by_gate.get("prd_review", ())), encoding="utf-8")
    files[2].write_text(_gate_markdown("TDD Gate", by_gate.get("tdd_review", ())), encoding="utf-8")
    files[3].write_text(_grill_markdown(events), encoding="utf-8")
    files[4].write_text(_issues_markdown(events), encoding="utf-8")
    files[5].write_text(_gate_markdown("Outcome Review Gate", by_gate.get("outcome_review", ())), encoding="utf-8")
    files[6].write_text(_transcript_markdown(run_id, task_id, events), encoding="utf-8")
    return DualAgentArtifactExport(status="ok", output_dir=out_dir, files=files)


def default_dual_agent_artifact_dir(cwd: str | Path, task_id: str) -> Path:
    return Path(cwd).resolve() / "docs" / "dual-agent" / _safe_path_component(task_id)


def _read_task_events(state: State, *, run_id: str, task_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in state.read_dual_agent_gate_events(run_id):
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("task_id") != task_id:
            continue
        events.append({
            "event_id": int(row["event_id"]),
            "ts": row["ts"],
            "kind": str(row["kind"]),
            "gate": str(payload.get("gate") or "unknown"),
            "payload": payload,
        })
    return events


def _events_by_gate(events: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], ...]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["gate"], []).append(event)
    return {gate: tuple(items) for gate, items in grouped.items()}


def _index_markdown(
    run_id: str,
    task_id: str,
    by_gate: dict[str, tuple[dict[str, Any], ...]],
) -> str:
    gates = "\n".join(
        f"- `{gate}`: {len(events)} event(s)"
        for gate, events in sorted(by_gate.items())
    )
    return "\n".join([
        f"# Dual-Agent Artifacts: {task_id}",
        "",
        f"- run_id: `{run_id}`",
        f"- task_id: `{task_id}`",
        "- source: supervisor SQLite event ledger",
        "",
        "## Files",
        "",
        "- [PRD](prd.md)",
        "- [TDD](tdd.md)",
        "- [Grill Findings](grill-findings.md)",
        "- [Issues](issues.md)",
        "- [Outcome Review](outcome-review.md)",
        "- [Transcript](transcript.md)",
        "",
        "## Gates",
        "",
        gates or "- No gate events recorded.",
        "",
    ])


def _gate_markdown(title: str, events: tuple[dict[str, Any], ...]) -> str:
    if not events:
        return f"# {title}\n\nNo events recorded for this gate.\n"
    sections = [f"# {title}", ""]
    for event in events:
        sections.append(_event_markdown(event))
    return "\n".join(sections)


def _transcript_markdown(run_id: str, task_id: str, events: list[dict[str, Any]]) -> str:
    sections = [
        f"# Dual-Agent Transcript: {task_id}",
        "",
        f"- run_id: `{run_id}`",
        f"- task_id: `{task_id}`",
        "- source: supervisor SQLite event ledger",
        "",
    ]
    for event in events:
        sections.append(_event_markdown(event))
    return "\n".join(sections)


def _event_markdown(event: dict[str, Any]) -> str:
    payload = event["payload"]
    lines = [
        f"## event_id: {event['event_id']}",
        "",
        f"- ts: `{event['ts']}`",
        f"- kind: `{event['kind']}`",
        f"- gate: `{event['gate']}`",
    ]
    if event["kind"] == "dual_agent_gate_round":
        round_payload = payload.get("round") if isinstance(payload.get("round"), dict) else {}
        lines.extend([
            f"- round_index: `{round_payload.get('round_index')}`",
            f"- codex_decision: `{round_payload.get('codex_decision')}`",
            f"- claude_decision: `{round_payload.get('claude_decision')}`",
            f"- codex_confidence: `{round_payload.get('codex_confidence')}`",
            f"- claude_confidence: `{round_payload.get('claude_confidence')}`",
            "",
            "### Objection",
            "",
            _text_or_none(round_payload.get("objection")),
            "",
        ])
        return "\n".join(lines)

    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    lines.extend([
        f"- status: `{payload.get('status')}`",
        f"- attempts: `{payload.get('attempts')}`",
        f"- handoff_packet_path: `{payload.get('handoff_packet_path')}`",
        "",
        "### Summary",
        "",
        _text_or_none(outcome.get("summary")),
        "",
        "### Decisions",
        "",
        _list_markdown(outcome.get("decisions")),
        "",
        "### Objections",
        "",
        _list_markdown(outcome.get("objections")),
        "",
        "### Specialists",
        "",
        _specialists_markdown(outcome.get("specialists")),
        "",
        "### Tests",
        "",
        _list_markdown(outcome.get("tests")),
        "",
        "### Claims",
        "",
        _list_markdown(outcome.get("claims")),
        "",
        "### Probes",
        "",
        _probes_markdown(payload.get("probes")),
        "",
    ])
    return "\n".join(lines)


def _grill_markdown(events: list[dict[str, Any]]) -> str:
    findings: list[str] = []
    for event in events:
        payload = event["payload"]
        if event["kind"] == "dual_agent_gate_round":
            round_payload = payload.get("round") if isinstance(payload.get("round"), dict) else {}
            objection = _clean_text(round_payload.get("objection"))
            if objection:
                findings.append(f"- event_id {event['event_id']} `{event['gate']}`: {objection}")
        else:
            outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
            for objection in outcome.get("objections") or []:
                text = _clean_text(objection)
                if text:
                    findings.append(f"- event_id {event['event_id']} `{event['gate']}`: {text}")
    body = "\n".join(findings) if findings else "- No unresolved grill findings recorded."
    return "\n".join([
        "# Grill Findings",
        "",
        "These findings are derived from dual-agent gate objections in the ledger.",
        "Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.",
        "",
        body,
        "",
    ])


def _issues_markdown(events: list[dict[str, Any]]) -> str:
    del events
    return "\n".join([
        "# Issues",
        "",
        "No issue artifacts were recorded in the dual-agent ledger.",
        "",
        "Future duo-agent runs must use the `prd-to-tdd` skill before implementation:",
        "",
        "- create or update the PRD artifact",
        "- run the PRD grill",
        "- slice issues with PRD promise blocks",
        "- write TDD plans for implementation issues",
        "- run the TDD grill",
        "",
    ])


def _specialists_markdown(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "- None recorded."
    rows = []
    for item in value:
        if not isinstance(item, dict):
            rows.append(f"- {item}")
            continue
        name = _clean_text(item.get("name")) or "unknown"
        decision = _clean_text(item.get("decision")) or "unknown"
        objection = _clean_text(item.get("objection"))
        suffix = f" — objection: {objection}" if objection else ""
        rows.append(f"- `{name}`: `{decision}`{suffix}")
    return "\n".join(rows)


def _probes_markdown(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "- None recorded."
    rows = []
    for probe_id, probe in sorted(value.items()):
        if not isinstance(probe, dict):
            rows.append(f"- `{probe_id}`: {probe}")
            continue
        rows.append(
            f"- `{probe_id}`: `{probe.get('status')}` / `{probe.get('reason')}`"
        )
    return "\n".join(rows)


def _list_markdown(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "- None recorded."
    return "\n".join(f"- {_clean_text(item)}" for item in value)


def _text_or_none(value: Any) -> str:
    return _clean_text(value) or "None recorded."


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _ascii_text(value.strip())
    return _ascii_text(json.dumps(value, sort_keys=True, default=str))


def _safe_path_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return safe or "dual-agent-task"


def _ascii_text(value: str) -> str:
    return value.replace("\u2014", "-").encode("ascii", "replace").decode("ascii")
