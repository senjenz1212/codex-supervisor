"""Markdown artifacts derived from dual-agent gate ledger events."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .failure_taxonomy import FAILURE_TAXONOMY_VERSION, detect_sequence_failures
from .state import State
from .trace_envelope import TRACE_ENVELOPE_SCHEMA_VERSION


@dataclass(frozen=True)
class DualAgentArtifactExport:
    status: str
    output_dir: Path
    files: tuple[Path, ...]


@dataclass(frozen=True)
class ScreenshotArtifact:
    path: str | Path
    label: str
    note: str = ""
    source: str = ""
    validation_status: str = ""
    validation_notes: str = ""


def export_dual_agent_run_artifacts(
    state: State,
    *,
    run_id: str,
    task_id: str,
    output_dir: str | Path,
    screenshots: tuple[ScreenshotArtifact, ...] = (),
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
        out_dir / "screenshots.md",
        out_dir / "outcome-review.md",
        out_dir / "interactions.md",
        out_dir / "transcript.md",
        out_dir / "transcript.jsonl",
        out_dir / "replay" / "manifest.json",
    )
    by_gate = _events_by_gate(events)
    screenshot_files = _copy_screenshots(out_dir, screenshots)
    files[10].parent.mkdir(parents=True, exist_ok=True)
    transcript_jsonl = _transcript_jsonl(events)
    files[0].write_text(_index_markdown(run_id, task_id, by_gate), encoding="utf-8")
    files[1].write_text(_gate_markdown("PRD Gate", by_gate.get("prd_review", ())), encoding="utf-8")
    files[2].write_text(_gate_markdown("TDD Gate", by_gate.get("tdd_review", ())), encoding="utf-8")
    files[3].write_text(_grill_markdown(events), encoding="utf-8")
    files[4].write_text(_issues_markdown(events), encoding="utf-8")
    files[5].write_text(_screenshots_markdown(screenshot_files), encoding="utf-8")
    files[6].write_text(_gate_markdown("Outcome Review Gate", by_gate.get("outcome_review", ())), encoding="utf-8")
    files[7].write_text(_interactions_markdown(run_id, task_id, events), encoding="utf-8")
    files[8].write_text(_transcript_markdown(run_id, task_id, events), encoding="utf-8")
    files[9].write_text(transcript_jsonl, encoding="utf-8")
    files[10].write_text(
        json.dumps(
            _replay_manifest(
                run_id=run_id,
                task_id=task_id,
                events=events,
                transcript_jsonl=transcript_jsonl,
            ),
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return DualAgentArtifactExport(status="ok", output_dir=out_dir, files=(*files, *tuple(path for path, _ in screenshot_files)))


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
        "- [Screenshots](screenshots.md)",
        "- [Outcome Review](outcome-review.md)",
        "- [Interactions](interactions.md)",
        "- [Transcript](transcript.md)",
        "- [Machine Transcript](transcript.jsonl)",
        "- [Replay Manifest](replay/manifest.json)",
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


def _transcript_jsonl(events: list[dict[str, Any]]) -> str:
    lines = [
        json.dumps(event, sort_keys=True, default=str)
        for event in events
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _replay_manifest(
    *,
    run_id: str,
    task_id: str,
    events: list[dict[str, Any]],
    transcript_jsonl: str,
) -> dict[str, Any]:
    event_ids = [int(event["event_id"]) for event in events]
    return {
        "schema_version": "dual-agent-replay-manifest/v1",
        "run_id": run_id,
        "task_id": task_id,
        "events_count": len(events),
        "event_ids": event_ids,
        "state": {
            "first_event_id": min(event_ids) if event_ids else 0,
            "last_event_id": max(event_ids) if event_ids else 0,
            "events_count_at_capture": len(events),
            "transcript_jsonl_sha256": sha256(transcript_jsonl.encode()).hexdigest(),
        },
        "schema_versions": {
            "trace_envelope": TRACE_ENVELOPE_SCHEMA_VERSION,
            "failure_taxonomy": FAILURE_TAXONOMY_VERSION,
            "agent_interaction": "dual-agent-interaction/v1",
            "replay_manifest": "dual-agent-replay-manifest/v1",
        },
        "files": {
            "index": "index.md",
            "interactions": "interactions.md",
            "transcript_markdown": "transcript.md",
            "transcript_jsonl": "transcript.jsonl",
        },
        "event_kinds": sorted({str(event["kind"]) for event in events}),
        "handoff_packets": _handoff_packet_manifest(events),
        "failure_summary": _run_failure_summary(events),
        "sequence_failures": detect_sequence_failures(events),
    }


def _handoff_packet_manifest(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, list[int]] = {}
    for event in events:
        payload = event["payload"]
        path = payload.get("handoff_packet_path")
        if not path:
            continue
        by_path.setdefault(str(path), []).append(int(event["event_id"]))

    packets: list[dict[str, Any]] = []
    for path_text, event_ids in sorted(by_path.items()):
        path = Path(path_text).expanduser()
        item: dict[str, Any] = {
            "path": path_text,
            "event_ids": event_ids,
        }
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            item.update({
                "status": "captured",
                "sha256": sha256(content.encode()).hexdigest(),
                "content": content,
            })
        else:
            item.update({
                "status": "missing_at_export",
                "sha256": None,
                "content": None,
            })
        packets.append(item)
    return packets


def _interactions_markdown(run_id: str, task_id: str, events: list[dict[str, Any]]) -> str:
    sections = [
        f"# Agent Interactions: {task_id}",
        "",
        f"- run_id: `{run_id}`",
        f"- task_id: `{task_id}`",
        "- source: supervisor SQLite event ledger",
        "- purpose: readable projection of the Codex, Claude Code, and optional Cursor decision dialogue",
        "",
    ]
    if not events:
        sections.extend(["No interaction events recorded.", ""])
        return "\n".join(sections)

    for index, event in enumerate(events, start=1):
        sections.append(_interaction_event_markdown(index, event))
    return "\n".join(sections)


def _interaction_event_markdown(index: int, event: dict[str, Any]) -> str:
    title = _title_from_gate(event["gate"])
    payload = event["payload"]
    if event["kind"] == "dual_agent_planning_validation":
        return _planning_validation_event_markdown(
            heading=f"## {index}. {title}",
            event=event,
            include_kind=False,
        )

    if event["kind"] == "dual_agent_interaction_message":
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
        return "\n".join([
            f"## {index}. {title}",
            "",
            f"- event_id: `{event['event_id']}`",
            f"- ts: `{event['ts']}`",
            f"- interaction_type: `{payload.get('message_type')}`",
            f"- sender: `{payload.get('sender')}`",
            f"- recipient: `{payload.get('recipient')}`",
            f"- round_index: `{payload.get('round_index')}`",
            f"- persona_id: `{_clean_text(payload.get('persona_id'))}`",
            f"- addresses: {_inline_markdown_value(payload.get('addresses') or [])}",
            "",
            "### Message",
            "",
            _text_or_none(payload.get("content")),
            "",
            "### Confidence",
            "",
            f"- value: `{confidence.get('value')}`",
            f"- source: `{confidence.get('source')}`",
            f"- rationale: {_text_or_none(confidence.get('rationale'))}",
            "",
            "Criteria:",
            "",
            _list_markdown(confidence.get("criteria")),
            "",
            "Evidence:",
            "",
            _list_markdown(confidence.get("evidence")),
            "",
            *_interaction_trace_sections(payload),
            *_trace_envelope_section(payload),
        ])

    if event["kind"] == "dual_agent_gate_round":
        round_payload = payload.get("round") if isinstance(payload.get("round"), dict) else {}
        return "\n".join([
            f"## {index}. {title}",
            "",
            f"- event_id: `{event['event_id']}`",
            f"- ts: `{event['ts']}`",
            f"- interaction_type: `round`",
            f"- round_index: `{round_payload.get('round_index')}`",
            "",
            "### Codex -> Claude Code",
            "",
            f"- Codex decision: `{round_payload.get('codex_decision')}`",
            f"- Codex confidence: `{round_payload.get('codex_confidence')}`",
            "",
            "### Claude Code -> Codex",
            "",
            f"- Claude decision: `{round_payload.get('claude_decision')}`",
            f"- Claude confidence: `{round_payload.get('claude_confidence')}`",
            "",
            "### Disagreement / Grill Finding",
            "",
            _text_or_none(round_payload.get("objection")),
            "",
        ])

    if event["kind"] == "tri_agent_cursor_review":
        return _cursor_review_event_markdown(
            heading=f"## {index}. {title}",
            event=event,
            include_kind=False,
        )

    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    return "\n".join([
        f"## {index}. {title}",
        "",
        f"- event_id: `{event['event_id']}`",
        f"- ts: `{event['ts']}`",
        f"- interaction_type: `gate_result`",
        f"- status: `{payload.get('status')}`",
        f"- attempts: `{payload.get('attempts')}`",
        "",
        "### Claude Code -> Codex",
        "",
        f"Outcome summary: {_text_or_none(outcome.get('summary'))}",
        "",
        "Decisions:",
        "",
        _list_markdown(outcome.get("decisions")),
        "",
        "Specialists:",
        "",
        _specialists_markdown(outcome.get("specialists")),
        "",
        "Objections:",
        "",
        _list_markdown(outcome.get("objections")),
        "",
        "### Validation",
        "",
        _probes_markdown(payload.get("probes")),
        "",
        "### Artifact Rigor",
        "",
        _artifact_rigor_markdown(payload.get("artifact_rigor")),
        "",
        *_trace_envelope_section(payload),
    ])


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

    if event["kind"] == "dual_agent_interaction_message":
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
        lines.extend([
            f"- interaction_type: `{payload.get('message_type')}`",
            f"- message_type: `{payload.get('message_type')}`",
            f"- sender: `{payload.get('sender')}`",
            f"- recipient: `{payload.get('recipient')}`",
            f"- round_index: `{payload.get('round_index')}`",
            f"- persona_id: `{_clean_text(payload.get('persona_id'))}`",
            f"- addresses: {_inline_markdown_value(payload.get('addresses') or [])}",
            "",
            "### Message",
            "",
            _text_or_none(payload.get("content")),
            "",
            "### Confidence",
            "",
            f"- value: `{confidence.get('value')}`",
            f"- source: `{confidence.get('source')}`",
            f"- rationale: {_text_or_none(confidence.get('rationale'))}",
            "",
            "### Criteria",
            "",
            _list_markdown(confidence.get("criteria")),
            "",
            "### Evidence",
            "",
            _list_markdown(confidence.get("evidence")),
            "",
            *_interaction_trace_sections(payload),
            *_trace_envelope_section(payload),
        ])
        return "\n".join(lines)

    if event["kind"] == "dual_agent_skill_receipt_validation":
        probe = payload.get("probe") if isinstance(payload.get("probe"), dict) else {}
        lines.extend([
            f"- status: `{payload.get('status')}`",
            "",
            "### Skill Receipt Validation",
            "",
            f"- probe_id: `{_clean_text(probe.get('probe_id'))}`",
            f"- status: `{_clean_text(probe.get('status'))}`",
            f"- reason: `{_clean_text(probe.get('reason'))}`",
            "",
            "Details:",
            "",
            _inline_markdown_value(probe.get("details") or {}),
            "",
            *_trace_envelope_section(payload),
        ])
        return "\n".join(lines)

    if event["kind"] == "dual_agent_planning_validation":
        return _planning_validation_event_markdown(
            heading=f"## event_id: {event['event_id']}",
            event=event,
            include_kind=True,
        )

    if event["kind"] == "tri_agent_cursor_review":
        return _cursor_review_event_markdown(
            heading=f"## event_id: {event['event_id']}",
            event=event,
            include_kind=True,
        )

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
        "### Artifact Rigor",
        "",
        _artifact_rigor_markdown(payload.get("artifact_rigor")),
        "",
        *_trace_envelope_section(payload),
    ])
    return "\n".join(lines)


def _planning_validation_event_markdown(
    *,
    heading: str,
    event: dict[str, Any],
    include_kind: bool,
) -> str:
    payload = event["payload"]
    lines = [
        heading,
        "",
        f"- event_id: `{event['event_id']}`",
        f"- ts: `{event['ts']}`",
    ]
    if include_kind:
        lines.extend([
            f"- kind: `{event['kind']}`",
            f"- gate: `{event['gate']}`",
        ])
    lines.extend([
        "- interaction_type: `planning_validation`",
        f"- gate: `{event['gate']}`",
        f"- validator_version: `{_clean_text(payload.get('validator_version'))}`",
        f"- verdict: `{_clean_text(payload.get('verdict'))}`",
        "",
        "### Checks",
        "",
        _planning_checks_markdown(payload.get("checks")),
        "",
        "### Artifacts",
        "",
        _list_markdown(payload.get("artifacts")),
        "",
        *_trace_envelope_section(payload),
    ])
    return "\n".join(lines)


def _cursor_review_event_markdown(
    *,
    heading: str,
    event: dict[str, Any],
    include_kind: bool,
) -> str:
    payload = event["payload"]
    cursor_review = _cursor_review_payload(payload)
    probe = cursor_review.get("probe") if isinstance(cursor_review.get("probe"), dict) else {}
    outcome = cursor_review.get("outcome") if isinstance(cursor_review.get("outcome"), dict) else {}
    lines = [
        heading,
        "",
        f"- event_id: `{event['event_id']}`",
        f"- ts: `{event['ts']}`",
    ]
    if include_kind:
        lines.extend([
            f"- kind: `{event['kind']}`",
            f"- gate: `{event['gate']}`",
        ])
    lines.extend([
        "- interaction_type: `cursor_review`",
        f"- gate: `{event['gate']}`",
        f"- accepted: `{cursor_review.get('accepted')}`",
        f"- model: `{_clean_text(cursor_review.get('model'))}`",
        f"- cursor_run_id: `{_clean_text(cursor_review.get('run_id'))}`",
        f"- agent_id: `{_clean_text(cursor_review.get('agent_id'))}`",
        f"- duration_ms: `{_clean_text(cursor_review.get('duration_ms'))}`",
        "",
        "### Cursor Probe",
        "",
        f"- probe_id: `{_clean_text(probe.get('probe_id'))}`",
        f"- status: `{_clean_text(probe.get('status'))}`",
        f"- reason: `{_clean_text(probe.get('reason'))}`",
        "",
        "### Cursor Outcome",
        "",
        _text_or_none(outcome.get("summary")),
        "",
        "Claims:",
        "",
        _list_markdown(outcome.get("claims")),
        "",
        "Decisions:",
        "",
        _list_markdown(outcome.get("decisions")),
        "",
        "Objections:",
        "",
        _list_markdown(outcome.get("objections")),
        "",
        "Specialists:",
        "",
        _specialists_markdown(outcome.get("specialists")),
        "",
    ])
    if cursor_review.get("transcript_tail"):
        lines.extend([
            "### Transcript Tail",
            "",
            _text_or_none(cursor_review.get("transcript_tail")),
            "",
        ])
    lines.extend(_trace_envelope_section(payload))
    return "\n".join(lines)


def _cursor_review_payload(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("cursor_review")
    if isinstance(nested, dict) and nested:
        return nested
    return {
        "accepted": payload.get("accepted"),
        "probe": payload.get("probe"),
        "outcome": payload.get("outcome"),
        "agent_id": payload.get("agent_id"),
        "run_id": payload.get("run_id") or payload.get("cursor_run_id"),
        "status": payload.get("status") or payload.get("cursor_status"),
        "model": payload.get("model"),
        "duration_ms": payload.get("duration_ms"),
        "transcript_tail": payload.get("transcript_tail"),
    }


def _interaction_trace_sections(payload: dict[str, Any]) -> list[str]:
    return [
        "### Claims",
        "",
        _list_markdown(payload.get("claims")),
        "",
        "### Objections",
        "",
        _list_markdown(payload.get("objections")),
        "",
        "### Questions",
        "",
        _list_markdown(payload.get("questions")),
        "",
        "### Tool Receipts",
        "",
        _list_markdown(payload.get("tool_receipts")),
        "",
        "### Evidence Refs",
        "",
        _list_markdown(payload.get("evidence_refs")),
        "",
        "### Raw Transcript Refs",
        "",
        _list_markdown(payload.get("raw_transcript_refs")),
        "",
        "### Would Change If",
        "",
        _text_or_none(payload.get("would_change_if")),
        "",
        "### Review Packet",
        "",
        _inline_markdown_value(payload.get("review_packet") or {}),
        "",
    ]


def _trace_envelope_section(payload: dict[str, Any]) -> list[str]:
    envelope = payload.get("trace_envelope") if isinstance(payload.get("trace_envelope"), dict) else {}
    if not envelope:
        return []
    failure = envelope.get("failure_taxonomy")
    lines = [
        "### Trace Envelope",
        "",
        f"- policy_verdict: `{_clean_text(envelope.get('policy_verdict'))}`",
    ]
    if isinstance(failure, dict):
        lines.extend([
            f"- failure_category: `{_clean_text(failure.get('category'))}`",
            f"- failure_subcategory: `{_clean_text(failure.get('subcategory'))}`",
            f"- failure_code: `{_clean_text(failure.get('code'))}`",
            f"- mast_code: `{_clean_text(failure.get('mast_code'))}`",
            f"- mast_mode: `{_clean_text(failure.get('mast_mode'))}`",
            f"- mast_category: `{_clean_text(failure.get('mast_category'))}`",
        ])
    else:
        lines.append("- failure_taxonomy: `None`")
    tool_calls = envelope.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        lines.extend([
            "",
            "Tool calls:",
            "",
            _list_markdown(tool_calls),
        ])
    lines.append("")
    return lines


def _run_failure_summary(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        envelope = event["payload"].get("trace_envelope")
        if not isinstance(envelope, dict):
            continue
        failure = envelope.get("failure_taxonomy")
        verdict = _clean_text(envelope.get("policy_verdict"))
        if verdict == "blocked" or isinstance(failure, dict):
            return {
                "event_id": int(event["event_id"]),
                "policy_verdict": verdict,
                "failure_taxonomy": failure,
            }
    return None


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


def _copy_screenshots(
    output_dir: Path,
    screenshots: tuple[ScreenshotArtifact, ...],
) -> list[tuple[Path, ScreenshotArtifact]]:
    copied: list[tuple[Path, ScreenshotArtifact]] = []
    if not screenshots:
        return copied
    screenshot_dir = output_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    for index, screenshot in enumerate(screenshots, start=1):
        source = Path(screenshot.path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"screenshot artifact missing: {source}")
        label = _clean_text(screenshot.label) or f"Screenshot {index}"
        suffix = source.suffix if source.suffix else ".png"
        filename = f"{index:02d}-{_safe_path_component(label).lower()}{suffix}"
        target = screenshot_dir / filename
        target.write_bytes(source.read_bytes())
        copied.append((
            target,
            ScreenshotArtifact(
                path=target,
                label=label,
                note=_clean_text(screenshot.note),
                source=_clean_text(screenshot.source),
                validation_status=_clean_text(screenshot.validation_status),
                validation_notes=_clean_text(screenshot.validation_notes),
            ),
        ))
    return copied


def _screenshots_markdown(screenshots: list[tuple[Path, ScreenshotArtifact]]) -> str:
    lines = [
        "# Screenshots",
        "",
        "Screenshots are generated or captured by Codex and stored as review evidence for user-facing changes.",
        "Outcome review should consider these images together with code diffs and test results.",
        "",
    ]
    if not screenshots:
        lines.extend([
            "No screenshot artifacts were supplied for this export.",
            "",
        ])
        return "\n".join(lines)

    for path, screenshot in screenshots:
        label = screenshot.label
        rel = f"screenshots/{path.name}"
        lines.extend([
            f"## {label}",
            "",
            f"![{label}]({rel})",
            "",
        ])
        metadata = []
        if screenshot.source:
            metadata.append(f"- source: `{screenshot.source}`")
        if screenshot.validation_status:
            metadata.append(f"- validation_status: `{screenshot.validation_status}`")
        if metadata:
            lines.extend(metadata + [""])
        if screenshot.note:
            lines.extend([screenshot.note, ""])
        if screenshot.validation_notes:
            lines.extend(["Validation notes:", "", screenshot.validation_notes, ""])
    return "\n".join(lines)


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


def _planning_checks_markdown(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "- None recorded."
    rows = []
    for check_id, status in sorted(value.items()):
        rows.append(f"- {_clean_text(check_id)}: {_clean_text(status)}")
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


def _artifact_rigor_markdown(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "- None recorded."
    rows = []
    for key in [
        "status",
        "reason",
        "artifact_policy",
        "required_artifacts",
        "present_artifacts",
        "missing_artifacts",
        "missing_artifact_paths",
        "required_prerequisite_gates",
        "accepted_prerequisite_gates",
        "missing_prerequisite_gates",
        "gate_statuses",
        "user_facing",
        "screenshots",
        "missing_screenshot_paths",
        "visual_validation",
    ]:
        if key not in value:
            continue
        rows.append(f"- {key}: {_inline_markdown_value(value.get(key))}")
    return "\n".join(rows) if rows else "- None recorded."


def _inline_markdown_value(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return "`[]`"
        return ", ".join(f"`{_clean_text(item)}`" for item in value)
    if isinstance(value, bool):
        return f"`{value}`"
    return f"`{_clean_text(value)}`"


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


def _title_from_gate(value: str) -> str:
    words = re.sub(r"[_-]+", " ", value).strip().split()
    if not words:
        return "Unknown Gate"
    acronyms = {"prd": "PRD", "tdd": "TDD"}
    return " ".join(acronyms.get(word.lower(), word.title()) for word in words)


def _ascii_text(value: str) -> str:
    return value.replace("\u2014", "-").encode("ascii", "replace").decode("ascii")
