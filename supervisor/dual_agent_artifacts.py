"""Markdown artifacts derived from dual-agent gate ledger events."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .failure_taxonomy import (
    FAILURE_TAXONOMY_VERSION,
    detect_sequence_failures,
    mast_coverage_matrix,
)
from .state import State
from .trace_envelope import TRACE_ENVELOPE_SCHEMA_VERSION, ensure_tool_call_timing


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
        out_dir / "triage.md",
        out_dir / "prd.md",
        out_dir / "tdd.md",
        out_dir / "grill-findings.md",
        out_dir / "issues.md",
        out_dir / "screenshots.md",
        out_dir / "outcome-review.md",
        out_dir / "interactions.md",
        out_dir / "transcript.md",
        out_dir / "transcript.jsonl",
        out_dir / "mast-coverage.md",
        out_dir / "replay" / "manifest.json",
        out_dir / "replay" / "workspace-snapshot.json",
        out_dir / "replay" / "mast-coverage.json",
    )
    by_gate = _events_by_gate(events)
    screenshot_files = _copy_screenshots(out_dir, screenshots)
    files[12].parent.mkdir(parents=True, exist_ok=True)
    transcript_jsonl = _transcript_jsonl(events)
    workspace_snapshot = _workspace_snapshot_manifest(events)
    mast_coverage = mast_coverage_matrix(events)
    files[0].write_text(_index_markdown(run_id, task_id, by_gate), encoding="utf-8")
    files[1].write_text(_triage_markdown(run_id, task_id, events), encoding="utf-8")
    files[2].write_text(_gate_markdown("PRD Gate", by_gate.get("prd_review", ())), encoding="utf-8")
    files[3].write_text(_gate_markdown("TDD Gate", by_gate.get("tdd_review", ())), encoding="utf-8")
    files[4].write_text(_grill_markdown(events), encoding="utf-8")
    files[5].write_text(_issues_markdown(events), encoding="utf-8")
    files[6].write_text(_screenshots_markdown(screenshot_files), encoding="utf-8")
    files[7].write_text(_gate_markdown("Outcome Review Gate", by_gate.get("outcome_review", ())), encoding="utf-8")
    files[8].write_text(_interactions_markdown(run_id, task_id, events), encoding="utf-8")
    files[9].write_text(_transcript_markdown(run_id, task_id, events), encoding="utf-8")
    files[10].write_text(transcript_jsonl, encoding="utf-8")
    files[11].write_text(_mast_coverage_markdown(mast_coverage), encoding="utf-8")
    files[12].write_text(
        json.dumps(
            _replay_manifest(
                run_id=run_id,
                task_id=task_id,
                events=events,
                transcript_jsonl=transcript_jsonl,
                workspace_snapshot=workspace_snapshot,
                mast_coverage=mast_coverage,
            ),
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    files[13].write_text(
        json.dumps(workspace_snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    files[14].write_text(
        json.dumps(mast_coverage, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return DualAgentArtifactExport(
        status="ok",
        output_dir=out_dir,
        files=(
            *files,
            *tuple(path for path, _ in screenshot_files),
            *_source_artifact_files(out_dir),
        ),
    )


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
        "- [Triage](triage.md)",
        "- [PRD Gate](prd.md)",
        "- [TDD Gate](tdd.md)",
        "- [Grill Findings](grill-findings.md)",
        "- [Issues](issues.md)",
        "- [Screenshots](screenshots.md)",
        "- [Outcome Review](outcome-review.md)",
        "- [Interactions](interactions.md)",
        "- [Transcript](transcript.md)",
        "- [Machine Transcript](transcript.jsonl)",
        "- [MAST Coverage](mast-coverage.md)",
        "- [Replay Manifest](replay/manifest.json)",
        "",
        "## Source Artifacts",
        "",
        "- [Source PRD](source/prd.md)",
        "- [Source PRD Grill Findings](source/grill-findings.md)",
        "- [Source Issues](source/issues.md)",
        "- [Source TDD](source/tdd.md)",
        "- [Source TDD Grill Findings](source/grill-findings-tdd.md)",
        "- [Source Implementation Plan](source/implementation-plan.md)",
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


def _triage_markdown(run_id: str, task_id: str, events: list[dict[str, Any]]) -> str:
    failure = _run_failure_summary(events)
    final_event = _latest_gate_result_event(events)
    final_payload = final_event["payload"] if final_event is not None else {}
    taxonomy = failure.get("failure_taxonomy") if isinstance(failure, dict) else None
    claim_probe = final_payload.get("claim_verification") if isinstance(final_payload.get("claim_verification"), dict) else {}
    claim_details = claim_probe.get("details") if isinstance(claim_probe.get("details"), dict) else {}
    failures = claim_details.get("failures") if isinstance(claim_details.get("failures"), list) else []
    claude_gate_status = _clean_text(final_payload.get("claude_gate_status") or final_payload.get("status"))
    supervisor_final_status = _clean_text(final_payload.get("supervisor_final_status") or final_payload.get("status"))
    top_calls = sorted(
        _all_trace_tool_calls(events),
        key=lambda item: int(item.get("duration_ms") or 0),
        reverse=True,
    )[:5]
    totals = _tool_call_totals(events)
    next_action = _next_safe_action(taxonomy, failures)

    lines = [
        f"# Triage: {task_id}",
        "",
        f"- run_id: `{run_id}`",
        f"- task_id: `{task_id}`",
        f"- final_event_id: `{_triage_final_event_id(failure, final_event)}`",
        f"- policy_verdict: `{failure.get('policy_verdict') if isinstance(failure, dict) else 'observed'}`",
        f"- claude_gate_status: `{claude_gate_status}`",
        f"- supervisor_final_status: `{supervisor_final_status}`",
        "",
        "## Run Totals",
        "",
        f"- unique_tool_calls: `{totals['unique_tool_calls']}`",
        f"- total_duration_ms: `{totals['total_duration_ms']}`",
        f"- total_duration_us: `{totals['total_duration_us']}`",
        f"- total_tokens_in: `{totals['total_tokens_in']}`",
        f"- total_tokens_out: `{totals['total_tokens_out']}`",
        f"- total_cost_usd: `{totals['total_cost_usd']}`",
        "",
        "## Root Cause",
        "",
    ]
    if isinstance(taxonomy, dict):
        lines.extend([
            f"- failure_code: `{_clean_text(taxonomy.get('code'))}`",
            f"- failure_category: `{_clean_text(taxonomy.get('category'))}`",
            f"- failure_subcategory: `{_clean_text(taxonomy.get('subcategory'))}`",
            f"- mast_code: `{_clean_text(taxonomy.get('mast_code'))}`",
            f"- mast_mode: `{_clean_text(taxonomy.get('mast_mode'))}`",
        ])
    else:
        lines.append("- No blocking failure taxonomy recorded.")

    lines.extend([
        "",
        "## Blocking Details",
        "",
        _list_markdown(failures),
        "",
        "## Slowest Tool Calls",
        "",
        _tool_call_triage_markdown(top_calls),
        "",
        "## Evidence Pointers",
        "",
        "- [Interactions](interactions.md)",
        "- [Transcript](transcript.md)",
        "- [Machine Transcript](transcript.jsonl)",
        "- [MAST Coverage](mast-coverage.md)",
        "- [Replay Manifest](replay/manifest.json)",
        "- [Source PRD](source/prd.md)",
        "- [Source PRD Grill Findings](source/grill-findings.md)",
        "- [Source Issues](source/issues.md)",
        "- [Source TDD](source/tdd.md)",
        "- [Source TDD Grill Findings](source/grill-findings-tdd.md)",
        "- [Source Implementation Plan](source/implementation-plan.md)",
        "",
        "## Next Safe Action",
        "",
        next_action,
        "",
    ])
    return "\n".join(lines)


def _replay_manifest(
    *,
    run_id: str,
    task_id: str,
    events: list[dict[str, Any]],
    transcript_jsonl: str,
    workspace_snapshot: dict[str, Any],
    mast_coverage: list[dict[str, Any]],
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
            "mast_coverage_markdown": "mast-coverage.md",
            "mast_coverage_json": "replay/mast-coverage.json",
            "workspace_snapshot": "replay/workspace-snapshot.json",
        },
        "event_kinds": sorted({str(event["kind"]) for event in events}),
        "handoff_packets": _handoff_packet_manifest(events),
        "workspace_snapshot": workspace_snapshot,
        "failure_summary": _run_failure_summary(events),
        "sequence_failures": detect_sequence_failures(events),
        "mast_coverage": mast_coverage,
        "tool_call_totals": _tool_call_totals(events),
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


def _workspace_snapshot_manifest(events: list[dict[str, Any]]) -> dict[str, Any]:
    handoff = _first_handoff_content(events)
    root_text = _clean_text(handoff.get("cwd")) if isinstance(handoff, dict) else ""
    if not root_text:
        return {"status": "not_found", "reason": "handoff_cwd_missing"}
    root = Path(root_text).expanduser()
    if not root.exists() or not root.is_dir():
        return {"status": "missing_at_export", "root": root_text}

    status_short = _run_git(root, "status", "--short")
    diff = _run_git(root, "diff", "--no-ext-diff") or ""
    head = _run_git(root, "rev-parse", "--short", "HEAD")
    diff_stat = _run_git(root, "diff", "--stat", "--no-ext-diff")
    return {
        "status": "captured",
        "root": str(root),
        "root_source": "handoff_cwd",
        "git": {
            "head": head,
            "head_sha": head,
            "head_ref": "HEAD",
            "head_label": "handoff_cwd_head",
            "status_short": status_short,
            "diff_sha256": sha256(diff.encode()).hexdigest(),
            "diff_bytes": len(diff.encode()),
            "diff_stat": diff_stat,
        },
        "file_tree_sha256": _file_tree_sha256(root),
        "source_artifact_hashes": _source_artifact_hashes(root, handoff),
    }


def _first_handoff_content(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in events:
        path_text = _clean_text(event["payload"].get("handoff_packet_path"))
        if not path_text:
            continue
        path = Path(path_text).expanduser()
        if not path.exists() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _run_git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _file_tree_sha256(root: Path) -> str:
    digest = sha256()
    for path in _snapshot_file_paths(root):
        if not path.is_file() or _excluded_snapshot_path(path, root):
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(str(len(data)).encode())
        digest.update(b"\0")
        digest.update(sha256(data).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _snapshot_file_paths(root: Path) -> list[Path]:
    git_paths = _git_visible_paths(root)
    if git_paths is not None:
        return git_paths
    return sorted(root.rglob("*"))


def _git_visible_paths(root: Path) -> list[Path] | None:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--modified", "--others", "--exclude-standard"],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    paths = [
        root / raw.decode("utf-8", errors="replace")
        for raw in completed.stdout.split(b"\0")
        if raw
    ]
    return sorted(paths)


def _source_artifact_hashes(root: Path, handoff: dict[str, Any]) -> dict[str, str]:
    artifacts = handoff.get("planning_artifacts") if isinstance(handoff, dict) else []
    if not isinstance(artifacts, list):
        return {}
    hashes: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        kind = _clean_text(artifact.get("kind"))
        path_text = _clean_text(artifact.get("path"))
        if not kind or not path_text:
            continue
        path = root / path_text
        if path.exists() and path.is_file() and not _excluded_snapshot_path(path, root):
            hashes[kind] = sha256(path.read_bytes()).hexdigest()
        elif _clean_text(artifact.get("sha256")):
            hashes[kind] = _clean_text(artifact.get("sha256"))
    return hashes


def _excluded_snapshot_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    parts = set(rel.split("/"))
    if parts & {
        ".git",
        ".venv",
        ".claude",
        ".cortex",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }:
        return True
    name = path.name.lower()
    if name.startswith(".env") or name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return any(token in name for token in ("secret", "credential", "token"))


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

    if event["kind"] == "dual_agent_dynamic_workflow_receipt_validation":
        return _dynamic_workflow_receipt_validation_event_markdown(
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
    if not outcome:
        return "\n".join([
            f"## {index}. {title}",
            "",
            f"- event_id: `{event['event_id']}`",
            f"- ts: `{event['ts']}`",
            f"- interaction_type: `gate_result`",
            f"- status: `{payload.get('status')}`",
            f"- attempts: `{payload.get('attempts')}`",
            "",
            *_gate_result_no_outcome_sections(payload),
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

    if event["kind"] == "dual_agent_dynamic_workflow_receipt_validation":
        return _dynamic_workflow_receipt_validation_event_markdown(
            heading=f"## event_id: {event['event_id']}",
            event=event,
            include_kind=True,
        )

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
    if not outcome:
        lines.extend([
            f"- status: `{payload.get('status')}`",
            f"- attempts: `{payload.get('attempts')}`",
            f"- handoff_packet_path: `{payload.get('handoff_packet_path')}`",
            "",
            *_gate_result_no_outcome_sections(payload),
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


def _dynamic_workflow_receipt_validation_event_markdown(
    *,
    heading: str,
    event: dict[str, Any],
    include_kind: bool,
) -> str:
    payload = event["payload"]
    probe = payload.get("probe") if isinstance(payload.get("probe"), dict) else {}
    details = probe.get("details") if isinstance(probe.get("details"), dict) else {}
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
        "- interaction_type: `dynamic_workflow_receipt_validation`",
        f"- gate: `{event['gate']}`",
        f"- status: `{payload.get('status')}`",
        "",
        "### P13 Dynamic Workflow Receipt Validation",
        "",
        f"- probe_id: `{_clean_text(probe.get('probe_id'))}`",
        f"- status: `{_clean_text(probe.get('status'))}`",
        f"- reason: `{_clean_text(probe.get('reason'))}`",
        f"- dynamic_workflow_task_class: `{_clean_text(details.get('dynamic_workflow_task_class'))}`",
        "",
        "Required gates:",
        "",
        _list_markdown(details.get("required_gates")),
        "",
        "Verified gates:",
        "",
        _list_markdown(details.get("verified_gates")),
        "",
        "Missing gates:",
        "",
        _list_markdown(details.get("missing_gates")),
        "",
        "Receipt ids:",
        "",
        _list_markdown(details.get("receipt_ids")),
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
    reasoning_ref = _cursor_reasoning_ref(cursor_review)
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
        f"- full_reasoning: `transcript.jsonl event {event['event_id']} transcript_tail`",
    ])
    if reasoning_ref:
        lines.append(f"- full_reasoning_ref: `{reasoning_ref}`")
    lines.extend([
        "",
        "### Cursor Probe",
        "",
        f"- probe_id: `{_clean_text(probe.get('probe_id'))}`",
        f"- status: `{_clean_text(probe.get('status'))}`",
        f"- reason: `{_clean_text(probe.get('reason'))}`",
        "",
        "### Cursor Outcome",
        "",
        _cursor_outcome_summary_markdown(cursor_review, outcome),
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


def _gate_result_no_outcome_sections(payload: dict[str, Any]) -> list[str]:
    reason = _gate_result_block_reason(payload)
    claude_status = _clean_text(payload.get("claude_gate_status"))
    if _claude_not_invoked(payload):
        lines = [
            "### Supervisor Block",
            "",
            "Claude Code was not invoked.",
            "",
            f"- reason: `{reason}`",
        ]
        if claude_status:
            lines.append(f"- claude_gate_status: `{claude_status}`")
        lines.append("")
        return lines
    lines = [
        "### Claude Code -> Codex",
        "",
        "No typed Claude outcome parsed.",
        "",
        "### Failure Details",
        "",
        f"- reason: `{reason}`",
    ]
    if claude_status:
        lines.append(f"- claude_gate_status: `{claude_status}`")
    lines.append("")
    return lines


def _claude_not_invoked(payload: dict[str, Any]) -> bool:
    if _clean_text(payload.get("claude_gate_status")) == "not_invoked":
        return True
    probes = payload.get("probes") if isinstance(payload.get("probes"), dict) else {}
    return int(payload.get("attempts") or 0) == 0 and "P2" not in probes


def _gate_result_block_reason(payload: dict[str, Any]) -> str:
    probes = payload.get("probes") if isinstance(payload.get("probes"), dict) else {}
    for probe_id in ("P2", "P3", "P_planning", "P1"):
        probe = probes.get(probe_id)
        if (
            isinstance(probe, dict)
            and _clean_text(probe.get("status")) == "red"
            and _clean_text(probe.get("reason"))
        ):
            return _clean_text(probe.get("reason"))
    escalation = payload.get("escalation") if isinstance(payload.get("escalation"), dict) else {}
    reason = _clean_text(escalation.get("reason"))
    if reason:
        return reason
    artifact_rigor = payload.get("artifact_rigor") if isinstance(payload.get("artifact_rigor"), dict) else {}
    reason = _clean_text(artifact_rigor.get("reason"))
    if reason:
        return reason
    for probe_id in ("P2", "P3", "P_planning", "P1"):
        probe = probes.get(probe_id)
        if isinstance(probe, dict) and _clean_text(probe.get("reason")):
            return _clean_text(probe.get("reason"))
    return _clean_text(payload.get("status")) or "unknown"


def _cursor_outcome_summary_markdown(
    cursor_review: dict[str, Any],
    outcome: dict[str, Any],
) -> str:
    summary = _clean_text(outcome.get("summary"))
    if summary:
        return summary
    probe = cursor_review.get("probe") if isinstance(cursor_review.get("probe"), dict) else {}
    lines = [
        "No typed Cursor outcome parsed.",
        "",
        "### Cursor Failure",
        "",
        f"- probe_id: `{_clean_text(probe.get('probe_id'))}`",
        f"- status: `{_clean_text(probe.get('status'))}`",
        f"- reason: `{_clean_text(probe.get('reason'))}`",
    ]
    details = probe.get("details")
    if _has_value(details):
        lines.append(f"- details: {_inline_markdown_value(details)}")
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
        "raw_transcript_refs": payload.get("raw_transcript_refs"),
    }


def _cursor_reasoning_ref(cursor_review: dict[str, Any]) -> str:
    refs = cursor_review.get("raw_transcript_refs")
    if not isinstance(refs, list):
        return ""
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if _clean_text(ref.get("kind")) == "cursor_transcript_fixture":
            return _clean_text(ref.get("ref"))
    return ""


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
        "### Critical Review",
        "",
        _inline_markdown_value(payload.get("critical_review") or {}),
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
            _tool_calls_markdown(tool_calls),
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


def _triage_final_event_id(
    failure: dict[str, Any] | None,
    final_event: dict[str, Any] | None,
) -> str:
    if isinstance(failure, dict) and failure.get("event_id") is not None:
        return str(failure["event_id"])
    if isinstance(final_event, dict) and final_event.get("event_id") is not None:
        return str(final_event["event_id"])
    return ""


def _latest_gate_result_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event["kind"] == "dual_agent_gate_result":
            return event
    return None


def _all_trace_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in events:
        envelope = event["payload"].get("trace_envelope")
        if not isinstance(envelope, dict):
            continue
        tool_calls = envelope.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            item = _normalise_trace_tool_call(call)
            item["event_id"] = int(event["event_id"])
            item["event_kind"] = event["kind"]
            calls.append(item)
    return calls


def _normalise_trace_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    item = ensure_tool_call_timing(call)
    result = item.get("result_summary") if isinstance(item.get("result_summary"), dict) else {}
    name = _clean_text(item.get("name"))
    if not item.get("probe_id"):
        if result.get("probe_id") or result.get("probe"):
            item["probe_id"] = result.get("probe_id") or result.get("probe")
        elif name == "verify_workflow_claims":
            item["probe_id"] = "P11"
        elif name == "verify_dynamic_workflow_receipts":
            item["probe_id"] = "P13"
        elif name == "validate_planning_artifacts":
            item["probe_id"] = "P_planning"
    failures = result.get("failures") if isinstance(result.get("failures"), list) else []
    receipt_ids = item.get("receipt_ids")
    if (receipt_ids is None or receipt_ids == []) and failures:
        item["receipt_ids"] = [
            f"missing:{_clean_text(failure)}"
            for failure in failures
            if _clean_text(failure)
        ]
    if not item.get("error") and _clean_text(item.get("status")).lower() in {"red", "failed", "blocked", "error"}:
        item["error"] = (
            result.get("reason")
            or result.get("probe_reason")
            or result.get("error")
            or item.get("reason")
        )
    if item.get("requested_model") is not None:
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        if "requested_model" not in args:
            item["args"] = {**args, "requested_model": item.get("requested_model")}
    if item.get("cost_usd") is None and result.get("cost_usd") is not None:
        item["cost_usd"] = result.get("cost_usd")
    return item


def _tool_call_totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    for call in _all_trace_tool_calls(events):
        key = _clean_text(call.get("tool_call_id")) or (
            f"{call.get('event_id')}:{call.get('name')}:{call.get('started_at_ms')}"
        )
        if key in by_id:
            by_id[key] = _merge_tool_call_for_totals(by_id[key], call)
        else:
            by_id[key] = call
    unique = list(by_id.values())
    return {
        "unique_tool_calls": len(unique),
        "total_duration_ms": sum(_int_value(call.get("duration_ms")) for call in unique),
        "total_duration_us": sum(_int_value(call.get("duration_us")) for call in unique),
        "total_tokens_in": sum(_int_value(call.get("tokens_in")) for call in unique),
        "total_tokens_out": sum(_int_value(call.get("tokens_out")) for call in unique),
        "total_cost_usd": round(sum(_tool_call_cost(call) for call in unique), 6),
    }


def _merge_tool_call_for_totals(
    existing: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in candidate.items():
        if _has_value(value) and not _has_value(merged.get(key)):
            merged[key] = value

    existing_result = (
        merged.get("result_summary")
        if isinstance(merged.get("result_summary"), dict)
        else {}
    )
    candidate_result = (
        candidate.get("result_summary")
        if isinstance(candidate.get("result_summary"), dict)
        else {}
    )
    if candidate_result:
        merged["result_summary"] = {
            **candidate_result,
            **existing_result,
        }

    for key in ("duration_ms", "duration_us", "tokens_in", "tokens_out", "cost_usd"):
        if _float_value(candidate.get(key)) > _float_value(merged.get(key)):
            merged[key] = candidate.get(key)
    return merged


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _mast_coverage_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# MAST Coverage",
        "",
        "This matrix lists every deterministic MAST-inspired mode the supervisor knows how to classify, plus whether the current run observed it.",
        "",
        "| code | category | mode | live_status | deterministic_status | trigger_surface | entrypoint | deterministic_probe | observed_sources |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {code} | {category} | {mode} | {status} | {deterministic_status} | {surface} | {entrypoint} | {probe} | {sources} |".format(
                code=_table_cell(row.get("mast_code")),
                category=_table_cell(row.get("mast_category")),
                mode=_table_cell(row.get("mast_mode")),
                status=_table_cell(row.get("status")),
                deterministic_status=_table_cell(row.get("deterministic_status")),
                surface=_table_cell(row.get("trigger_surface")),
                entrypoint=_table_cell(row.get("entrypoint")),
                probe=_table_cell(row.get("deterministic_probe")),
                sources=_table_cell(row.get("sources")),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _tool_call_triage_markdown(calls: list[dict[str, Any]]) -> str:
    if not calls:
        return "- None recorded."
    rows = [
        "| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for call in calls:
        rows.append(
            "| {event_id} | {tool_call_id} | {parent_tool_call_id} | {references_tool_call_id} | {name} | {status} | {duration_ms} | {duration_us} | {tokens_in} | {tokens_out} | {probe_id} | {receipt_ids} | {args} | {result} | {error} |".format(
                event_id=_table_cell(call.get("event_id")),
                tool_call_id=_table_cell(call.get("tool_call_id")),
                parent_tool_call_id=_table_cell(call.get("parent_tool_call_id")),
                references_tool_call_id=_table_cell(call.get("references_tool_call_id")),
                name=_table_cell(call.get("name")),
                status=_table_cell(call.get("status")),
                duration_ms=_table_cell(call.get("duration_ms")),
                duration_us=_table_cell(call.get("duration_us")),
                tokens_in=_table_cell(call.get("tokens_in")),
                tokens_out=_table_cell(call.get("tokens_out")),
                probe_id=_table_cell(call.get("probe_id")),
                receipt_ids=_table_cell(call.get("receipt_ids")),
                args=_table_cell(call.get("args")),
                result=_table_cell(call.get("result_summary")),
                error=_table_cell(call.get("error")),
            )
        )
    return "\n".join(rows)


def _tool_calls_markdown(value: list[Any]) -> str:
    calls = [_normalise_trace_tool_call(item) for item in value if isinstance(item, dict)]
    if not calls:
        return "- None recorded."
    lines = [
        "| tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for call in calls:
        lines.append(
            "| {tool_call_id} | {parent_tool_call_id} | {references_tool_call_id} | {name} | {status} | {duration} | {duration_us} | {tokens_in} | {tokens_out} | {probe_id} | {receipt_ids} | {args} | {result} | {error} |".format(
                tool_call_id=_table_cell(call.get("tool_call_id")),
                parent_tool_call_id=_table_cell(call.get("parent_tool_call_id")),
                references_tool_call_id=_table_cell(call.get("references_tool_call_id")),
                name=_table_cell(call.get("name")),
                status=_table_cell(call.get("status")),
                duration=_table_cell(call.get("duration_ms")),
                duration_us=_table_cell(call.get("duration_us")),
                tokens_in=_table_cell(call.get("tokens_in")),
                tokens_out=_table_cell(call.get("tokens_out")),
                probe_id=_table_cell(call.get("probe_id")),
                receipt_ids=_table_cell(call.get("receipt_ids")),
                args=_table_cell(call.get("args")),
                result=_table_cell(call.get("result_summary")),
                error=_table_cell(call.get("error")),
            )
        )
    return "\n".join(lines)


def _next_safe_action(taxonomy: Any, failures: list[Any]) -> str:
    code = taxonomy.get("code") if isinstance(taxonomy, dict) else ""
    if code == "workflow_claim_verification_failed":
        return (
            "Provide matching test and git-diff receipts, then rerun outcome review. "
            f"Missing evidence: {_inline_markdown_value(failures)}."
        )
    if isinstance(taxonomy, dict):
        return (
            "Inspect the failure event, resolve the named taxonomy blocker, "
            "then rerun the blocked gate."
        )
    return "Inspect the latest gate result and replay manifest before advancing."


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


def _source_artifact_files(output_dir: Path) -> tuple[Path, ...]:
    source_dir = output_dir / "source"
    if not source_dir.exists() or not source_dir.is_dir():
        return ()
    return tuple(path for path in sorted(source_dir.rglob("*")) if path.is_file())


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


def _table_cell(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ")


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _tool_call_cost(call: dict[str, Any]) -> float:
    if call.get("cost_usd") is not None:
        return _float_value(call.get("cost_usd"))
    result = call.get("result_summary") if isinstance(call.get("result_summary"), dict) else {}
    return _float_value(result.get("cost_usd"))


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
