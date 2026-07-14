"""Run-manifest provenance for replayable supervisor executions."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


EXECUTION_PROVENANCE_SCHEMA_VERSION = "dual-agent-execution-provenance/v1"
REQUIRED_COMPONENT_CATEGORIES = (
    "prompts",
    "tool_contracts",
    "containers",
    "cli",
    "evaluators",
)
_MODEL_ALIASES = {
    "auto",
    "default",
    "haiku",
    "latest",
    "opus",
    "proxy-default",
    "sonnet",
}
_COMPONENT_CATEGORY_ALIASES = {
    "prompt": "prompts",
    "prompts": "prompts",
    "tool_contract": "tool_contracts",
    "tool_contracts": "tool_contracts",
    "container": "containers",
    "containers": "containers",
    "image": "containers",
    "cli": "cli",
    "evaluator": "evaluators",
    "evaluators": "evaluators",
}
_BOUND_REFERENCE_PREFIXES = (
    "artifact:",
    "event:",
    "ledger:",
    "provider-response:",
    "receipt:",
)
_RUNTIME_STATE_FILENAMES = frozenset({
    "experiments.db",
    "grades.db",
    "state.db",
    "state.sqlite",
    "state.sqlite3",
    "trace.db",
})
_WORKSPACE_OVERLAY_MAX_CONTENT_BYTES = 1024 * 1024

_RUNTIME_STATE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_RUNTIME_STATE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")
_RUNTIME_STATE_DIRECTORIES = frozenset({
    ".codex-supervisor",
    ".orchestrator-state",
})


def capture_acceptance_evidence(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Freeze the handoff and public workspace when an accepted result is written."""
    if not _payload_is_accepted(payload):
        return None
    handoff_path_text = str(payload.get("handoff_packet_path") or "").strip()
    evidence: dict[str, Any] = {
        "schema_version": "dual-agent-acceptance-evidence/v1",
        "status": "incomplete",
        "snapshot_ref": None,
        "snapshot_sha256": None,
        "handoff_packet_path": handoff_path_text,
        "handoff_packet_sha256": None,
        "workspace_root": None,
        "workspace_commit": None,
    }
    if not handoff_path_text:
        return evidence

    handoff_path = Path(handoff_path_text).expanduser()
    try:
        handoff_bytes = handoff_path.read_bytes()
    except OSError as exc:
        evidence["reason"] = f"handoff_missing:{type(exc).__name__}"
        return evidence

    handoff_content = handoff_bytes.decode("utf-8", errors="replace")
    handoff_packet = {
        "path": handoff_path_text,
        "status": "captured",
        "sha256": sha256(handoff_bytes).hexdigest(),
        "content": handoff_content,
    }
    evidence["handoff_packet_sha256"] = handoff_packet["sha256"]
    try:
        handoff = json.loads(handoff_content or "{}")
    except json.JSONDecodeError:
        handoff = {}
    if not isinstance(handoff, dict):
        handoff = {}
    root_text = str(handoff.get("cwd") or "").strip()
    task_id = str(
        payload.get("task_id") or handoff.get("task_id") or ""
    ).strip()
    workspace_snapshot = (
        {
            "status": "not_found",
            "capture_source": "accepted_gate_event",
            "reason": "handoff_cwd_missing",
        }
        if not root_text
        else capture_workspace_snapshot(
            Path(root_text),
            handoff=handoff,
            capture_source="accepted_gate_event",
            excluded_roots=_acceptance_artifact_roots(
                Path(root_text),
                task_id=task_id,
            ),
        )
    )
    snapshot_payload = {
        "schema_version": "dual-agent-acceptance-snapshot/v1",
        "handoff_packet": handoff_packet,
        "workspace_snapshot": workspace_snapshot,
    }
    snapshot_bytes = json.dumps(
        snapshot_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    snapshot_sha256 = sha256(snapshot_bytes).hexdigest()
    snapshot_path = (
        handoff_path.parent
        / "acceptance-snapshots"
        / f"{snapshot_sha256}.json"
    )
    try:
        _write_content_addressed_snapshot(snapshot_path, snapshot_bytes)
    except OSError as exc:
        evidence["reason"] = f"snapshot_write_failed:{type(exc).__name__}"
        return evidence
    evidence.update({
        "snapshot_ref": str(snapshot_path),
        "snapshot_sha256": snapshot_sha256,
        "workspace_root": workspace_snapshot.get("root"),
        "workspace_commit": (
            workspace_snapshot.get("git", {}).get("head_sha")
            if isinstance(workspace_snapshot.get("git"), dict)
            else None
        ),
    })
    if (
        workspace_snapshot.get("status") == "captured"
        and isinstance(workspace_snapshot.get("immutable_snapshot"), dict)
        and workspace_snapshot["immutable_snapshot"].get("status") == "captured"
    ):
        evidence["status"] = "captured"
    return evidence


def _acceptance_artifact_roots(
    root: Path,
    *,
    task_id: str,
) -> tuple[Path, ...]:
    safe_task_id = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-")
    if safe_task_id in {"", ".", ".."}:
        safe_task_id = "dual-agent-task"
    root_resolved = root.expanduser().resolve()
    artifact_root = root_resolved / "docs" / "dual-agent"
    candidate = artifact_root / safe_task_id
    try:
        candidate.relative_to(artifact_root)
    except ValueError:
        return ()
    return (candidate,)


def capture_workspace_snapshot(
    root: Path,
    *,
    handoff: dict[str, Any] | None = None,
    capture_source: str,
    excluded_roots: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Capture a content-addressed public-workspace reconstruction snapshot."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        return {
            "status": "missing",
            "root": str(root),
            "capture_source": capture_source,
        }
    head = _git_output(root, "rev-parse", "HEAD")
    status_short = _git_output(root, "status", "--short")
    diff = _git_output(root, "diff", "--no-ext-diff", "HEAD")
    diff_stat = _git_output(root, "diff", "--stat", "--no-ext-diff", "HEAD")
    normalized_exclusions = _normalize_relative_exclusions(root, excluded_roots)
    source_artifact_paths = _source_artifact_paths(root, handoff or {})
    immutable_snapshot = build_workspace_overlay(
        root,
        base_commit=head,
        excluded_roots=normalized_exclusions,
        included_paths=source_artifact_paths,
    )
    return {
        "status": "captured" if head else "unavailable",
        "root": str(root),
        "root_source": "handoff_cwd",
        "capture_source": capture_source,
        "git": {
            "head": head,
            "head_sha": head,
            "commit_sha": head,
            "head_ref": "HEAD",
            "head_label": "handoff_cwd_head",
            "status_short": status_short,
            "diff_sha256": sha256(diff.encode()).hexdigest(),
            "diff_bytes": len(diff.encode()),
            "diff_stat": diff_stat,
        },
        "file_tree_sha256": _workspace_content_sha256(
            root,
            head=head,
            immutable_snapshot=immutable_snapshot,
        ),
        "source_artifact_hashes": _source_artifact_hashes(
            root,
            handoff or {},
        ),
        "immutable_snapshot": immutable_snapshot,
    }


def execution_provenance_issues(provenance: Any) -> list[str]:
    """Return fail-closed reasons that make a manifest non-authoritative."""
    if not isinstance(provenance, dict):
        return ["execution_provenance_missing"]
    issues: list[str] = []
    if provenance.get("schema_version") != EXECUTION_PROVENANCE_SCHEMA_VERSION:
        issues.append("execution_provenance_schema_incompatible")
    if provenance.get("status") != "complete":
        issues.append("execution_provenance_incomplete")
    for field in (
        "unresolved_model_lanes",
        "missing_component_categories",
        "missing_tool_contracts",
        "invalid_tool_contracts",
        "workspace_issues",
    ):
        value = provenance.get(field)
        if not isinstance(value, list) or value:
            issues.append(f"{field}_not_clear")

    model_resolutions = provenance.get("model_resolutions")
    if not isinstance(model_resolutions, list) or not model_resolutions:
        issues.append("model_resolutions_missing")
    elif any(
        not isinstance(item, dict)
        or item.get("exact_model_identity") is not True
        or not _is_exact_model_identity(item.get("resolved_model"))
        or item.get("resolution_source") != "response_model"
        or not _is_bound_reference(item.get("provider_response_source"))
        for item in model_resolutions
    ):
        issues.append("model_resolutions_not_exact")

    required_tool_contracts = provenance.get("required_tool_contracts")
    if (
        not isinstance(required_tool_contracts, list)
        or not required_tool_contracts
        or any(
            not isinstance(name, str) or not name.strip()
            for name in required_tool_contracts
        )
    ):
        issues.append("required_tool_contracts_missing")

    component_hashes = provenance.get("component_hashes")
    if not isinstance(component_hashes, dict):
        issues.append("component_hashes_missing")
    else:
        for category in REQUIRED_COMPONENT_CATEGORIES:
            components = component_hashes.get(category)
            if not isinstance(components, list) or not components:
                issues.append(f"component_hashes_missing:{category}")
                continue
            if any(
                not isinstance(component, dict)
                or not _is_sha256(component.get("sha256"))
                for component in components
            ):
                issues.append(f"component_hash_invalid:{category}")
            if any(
                not _verified_component_artifact(category, component)
                for component in components
            ):
                issues.append(f"component_artifact_invalid:{category}")
        tool_contracts = component_hashes.get("tool_contracts")
        if (
            not isinstance(tool_contracts, list)
            or not tool_contracts
            or any(
                not _verified_tool_contract_component(component)
                for component in tool_contracts
            )
        ):
            issues.append("tool_contract_artifacts_invalid")
        elif isinstance(required_tool_contracts, list):
            verified_by_name: dict[str, set[str]] = {}
            for component in tool_contracts:
                details = component["details"]
                verified_by_name.setdefault(
                    str(details["tool_name"]),
                    set(),
                ).add(str(component["sha256"]))
            if (
                set(required_tool_contracts) - set(verified_by_name)
                or any(len(digests) != 1 for digests in verified_by_name.values())
            ):
                issues.append("tool_contract_artifacts_invalid")
    return issues


def build_execution_provenance(
    *,
    events: list[dict[str, Any]],
    workspace_snapshot: dict[str, Any],
    handoff_packets: Iterable[dict[str, Any]] = (),
    provider_model_resolutions: Iterable[dict[str, Any]] = (),
    canonical_tool_contracts: Iterable[dict[str, Any]] = (),
    runtime_component_receipts: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build deterministic model and component provenance for one run.

    ``canonical_tool_contracts`` must contain the exact serialized definition
    bytes used by the runtime plus their independently captured ``sha256``.
    Observed tool-call shapes are never treated as contract definitions.
    Each item supplies ``tool_name``, ``canonical_bytes`` (or
    ``canonical_bytes_base64``), and ``sha256``.

    ``provider_model_resolutions`` is the explicit handoff for exact identities
    returned by providers. Requested or configured model names remain useful
    route evidence, but they are never promoted to exact identities. Each item
    supplies the lane coordinates plus ``resolved_model`` and a bound
    ``provider_response_receipt_ref`` (or equivalent receipt reference).

    ``runtime_component_receipts`` contains execution-time digests or canonical
    bytes for container, CLI, and evaluator components. Export-time filesystem
    inspection and caller-provided hashes embedded in tool arguments are never
    promoted to execution provenance.
    """
    model_resolutions = _model_resolutions(
        events,
        provider_model_resolutions,
    )
    (
        tool_contract_components,
        required_tool_contracts,
        missing_tool_contracts,
        invalid_tool_contracts,
    ) = _tool_contract_components(events, canonical_tool_contracts)
    component_receipts = _runtime_component_receipt_index(
        runtime_component_receipts
    )
    component_hashes = {
        "prompts": _prompt_components(events, handoff_packets),
        "tool_contracts": tool_contract_components,
        "containers": _container_components(events, component_receipts),
        "cli": _cli_components(events, component_receipts),
        "evaluators": _evaluator_components(events, component_receipts),
    }
    unresolved_models = (
        [
            item["lane_id"]
            for item in model_resolutions
            if (
                not _is_exact_model_identity(item.get("resolved_model"))
                or item.get("exact_model_identity") is not True
                or not _is_bound_reference(
                    item.get("provider_response_source")
                )
            )
        ]
        if model_resolutions
        else ["model-resolution:not-recorded"]
    )
    missing_components = [
        category
        for category in REQUIRED_COMPONENT_CATEGORIES
        if (
            _component_category_missing(
                category,
                component_hashes.get(category) or [],
            )
            or (
                category == "tool_contracts"
                and (missing_tool_contracts or invalid_tool_contracts)
            )
        )
    ]
    workspace_issues = _workspace_snapshot_issues(workspace_snapshot)
    return {
        "schema_version": EXECUTION_PROVENANCE_SCHEMA_VERSION,
        "status": (
            "complete"
            if (
                not unresolved_models
                and not missing_components
                and not workspace_issues
                and not missing_tool_contracts
                and not invalid_tool_contracts
            )
            else "incomplete"
        ),
        "workspace_root": str(workspace_snapshot.get("root") or ""),
        "model_resolutions": model_resolutions,
        "component_hashes": component_hashes,
        "unresolved_model_lanes": unresolved_models,
        "missing_component_categories": missing_components,
        "required_tool_contracts": required_tool_contracts,
        "missing_tool_contracts": missing_tool_contracts,
        "invalid_tool_contracts": invalid_tool_contracts,
        "workspace_issues": workspace_issues,
    }


def _component_category_missing(
    category: str,
    components: list[dict[str, Any]],
) -> bool:
    if not components:
        return True
    return any(
        not _verified_component_artifact(category, component)
        for component in components
    )


def _payload_is_accepted(payload: dict[str, Any]) -> bool:
    for key in ("supervisor_final_status", "status", "claude_gate_status"):
        value = str(payload.get(key) or "").strip().lower()
        if value:
            return value in {"accept", "accepted"}
    outcome = payload.get("outcome")
    return (
        isinstance(outcome, dict)
        and str(outcome.get("decision") or "").strip().lower() in {"accept", "accepted"}
    )


def _write_content_addressed_snapshot(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and path.read_bytes() == content:
            return
        raise OSError("content-addressed acceptance snapshot collision")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _workspace_snapshot_issues(
    workspace_snapshot: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    if workspace_snapshot.get("status") != "captured":
        issues.append("workspace_snapshot_not_captured")
        return issues
    if workspace_snapshot.get("capture_source") != "accepted_gate_event":
        issues.append("workspace_snapshot_not_acceptance_bound")
    root = Path(str(workspace_snapshot.get("root") or "")).expanduser()
    if not root.is_dir():
        issues.append("workspace_root_unavailable")
    git = (
        workspace_snapshot.get("git")
        if isinstance(workspace_snapshot.get("git"), dict)
        else {}
    )
    if not _is_git_commit(git.get("head_sha") or git.get("head")):
        issues.append("workspace_commit_unpinned")
    if not _is_sha256(workspace_snapshot.get("file_tree_sha256")):
        issues.append("workspace_tree_unhashed")
    immutable = (
        workspace_snapshot.get("immutable_snapshot")
        if isinstance(workspace_snapshot.get("immutable_snapshot"), dict)
        else {}
    )
    if (
        immutable.get("status") != "captured"
        or not _is_sha256(immutable.get("sha256"))
    ):
        issues.append("workspace_overlay_unavailable")
    return issues


def build_workspace_overlay(
    root: Path,
    *,
    base_commit: str,
    excluded_roots: Iterable[str | Path] = (),
    included_paths: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Capture the public worktree delta needed to reconstruct a dirty run."""
    changed_paths = _git_changed_paths(root)
    exclusions = _normalize_relative_exclusions(root, excluded_roots)
    inclusions = _normalize_relative_exclusions(root, included_paths)
    if changed_paths is None or not base_commit:
        payload = {
            "schema_version": "dual-agent-workspace-overlay/v1",
            "status": "unavailable",
            "base_commit": base_commit,
            "scope": "replay_public_workspace",
            "entries": [],
            "excluded_paths": [],
        }
        return {**payload, "sha256": _canonical_sha256(payload)}

    entries: list[dict[str, Any]] = []
    excluded_paths: list[str] = []
    for relative in changed_paths:
        path = root / relative
        if _excluded_snapshot_path(
            path,
            root,
            excluded_roots=exclusions,
            included_paths=inclusions,
        ):
            excluded_paths.append(relative.as_posix())
            continue
        entries.append(_workspace_overlay_entry(path, root=root))
    omitted_paths = sorted(
        str(entry["path"])
        for entry in entries
        if entry.get("content_omitted")
    )
    payload = {
        "schema_version": "dual-agent-workspace-overlay/v1",
        "status": "captured" if not omitted_paths else "incomplete",
        "base_commit": base_commit,
        "scope": "replay_public_workspace",
        "entries": sorted(entries, key=lambda item: str(item["path"])),
        "excluded_paths": sorted(excluded_paths),
        "omitted_paths": omitted_paths,
    }
    return {**payload, "sha256": _canonical_sha256(payload)}


def _model_resolutions(
    events: list[dict[str, Any]],
    provider_model_resolutions: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    candidates = [
        *_model_candidates(events),
        *_provider_model_candidates(provider_model_resolutions),
    ]
    by_lane: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        lane_key = (
            int(candidate.get("event_id") or 0),
            str(candidate.get("gate") or "unknown"),
            str(candidate.get("lane") or "model"),
        )
        by_lane.setdefault(lane_key, []).append(
            _model_resolution_record(candidate)
        )

    resolutions: list[dict[str, Any]] = []
    for lane_key, records in by_lane.items():
        exact_models = {
            str(record.get("resolved_model") or "")
            for record in records
            if record.get("exact_model_identity") is True
        }
        record = max(records, key=_resolution_rank)
        if len(exact_models) > 1:
            record = {
                **record,
                "resolved_model": "",
                "resolution_source": "conflicting_provider_responses",
                "exact_model_identity": False,
                "provider_returned_models": sorted(exact_models),
            }
        else:
            record = {
                **record,
                "requested_model": next(
                    (
                        str(item.get("requested_model") or "")
                        for item in records
                        if str(item.get("requested_model") or "")
                    ),
                    str(record.get("requested_model") or ""),
                ),
                "observed_model": next(
                    (
                        str(item.get("observed_model") or "")
                        for item in records
                        if str(item.get("observed_model") or "")
                    ),
                    str(record.get("observed_model") or ""),
                ),
                "model_source": next(
                    (
                        str(item.get("model_source") or "")
                        for item in records
                        if str(item.get("model_source") or "")
                    ),
                    str(record.get("model_source") or ""),
                ),
            }
        lane_identity = {
            "event_id": lane_key[0],
            "gate": lane_key[1],
            "lane": lane_key[2],
        }
        lane_id = next(
            (
                str(item.get("lane_id") or "")
                for item in records
                if str(item.get("lane_id") or "")
            ),
            _stable_id("model-lane", lane_identity),
        )
        resolutions.append({**record, "lane_id": lane_id})
    return sorted(
        resolutions,
        key=lambda item: (
            int(item["event_id"]),
            str(item["gate"]),
            str(item["lane"]),
        ),
    )


def _model_resolution_record(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    requested = str(candidate.get("requested_model") or "").strip()
    observed = str(candidate.get("observed_model") or "").strip()
    provider_returned = str(
        candidate.get("provider_returned_model") or ""
    ).strip()
    provider_response_source = str(
        candidate.get("provider_response_source") or ""
    ).strip()
    runtime = str(candidate.get("runtime") or "").strip()
    provider = str(candidate.get("provider_family") or "").strip()
    resolved_model, resolution_source, exact = _resolve_model(
        requested_model=requested,
        observed_model=observed,
        provider_returned_model=provider_returned,
        provider_response_source=provider_response_source,
        runtime=runtime,
        provider_family=provider,
    )
    return {
        "event_id": int(candidate.get("event_id") or 0),
        "gate": str(candidate.get("gate") or "unknown"),
        "lane": str(candidate.get("lane") or "model"),
        "runtime": runtime or "unknown",
        "provider_family": provider or _provider_for(runtime, resolved_model),
        "requested_model": requested or observed,
        "observed_model": observed,
        "resolved_model": resolved_model,
        "model_source": str(candidate.get("model_source") or ""),
        "provider_response_source": provider_response_source,
        "resolution_source": resolution_source,
        "exact_model_identity": exact,
        "lane_id": str(candidate.get("lane_id") or ""),
    }


def _provider_model_candidates(
    provider_model_resolutions: Iterable[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    for index, resolution in enumerate(provider_model_resolutions):
        if not isinstance(resolution, dict):
            yield {
                "event_id": 0,
                "gate": "unknown",
                "lane": f"provider-model-{index}",
            }
            continue
        resolved_model = resolution.get("resolved_model")
        provider_response_source = _provider_response_reference(resolution)
        yield {
            "event_id": resolution.get("event_id") or 0,
            "gate": resolution.get("gate") or "unknown",
            "lane": (
                resolution.get("lane")
                or resolution.get("worker_id")
                or resolution.get("reviewer_id")
                or f"provider-model-{index}"
            ),
            "runtime": resolution.get("runtime") or "",
            "provider_family": resolution.get("provider_family") or "",
            "requested_model": resolution.get("requested_model") or "",
            "observed_model": resolved_model,
            "provider_returned_model": resolved_model,
            "model_source": resolution.get("model_source") or "",
            "provider_response_source": provider_response_source,
            "lane_id": resolution.get("lane_id") or "",
        }


def _model_candidates(events: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for event in events:
        event_id = int(event.get("event_id") or 0)
        gate = str(event.get("gate") or event.get("payload", {}).get("gate") or "unknown")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for index, call in enumerate(_tool_calls(payload)):
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            result = (
                call.get("result_summary")
                if isinstance(call.get("result_summary"), dict)
                else {}
            )
            requested = (
                args.get("requested_model")
                or args.get("model_alias")
                or args.get("model")
            )
            provider_returned, response_source = _first_present_value(
                ("trace_envelope.tool_call.resolved_model", call.get("resolved_model")),
                (
                    "trace_envelope.result_summary.resolved_model",
                    result.get("resolved_model"),
                ),
                ("trace_envelope.tool_call.served_model", call.get("served_model")),
                (
                    "trace_envelope.result_summary.served_model",
                    result.get("served_model"),
                ),
                ("trace_envelope.result_summary.model", result.get("model")),
            )
            provider_response_source = (
                _provider_response_reference(call)
                or _provider_response_reference(result)
                or _bound_event_reference(event_id, response_source)
            )
            observed = provider_returned or call.get("model")
            if requested in (None, "") and observed in (None, ""):
                continue
            name = str(call.get("name") or f"tool-call-{index}")
            runtime = str(
                args.get("runtime")
                or call.get("runtime")
                or _runtime_for_tool(name)
            )
            yield {
                "event_id": event_id,
                "gate": gate,
                "lane": name,
                "runtime": runtime,
                "provider_family": str(
                    args.get("provider_family")
                    or call.get("provider_family")
                    or ""
                ),
                "requested_model": requested,
                "observed_model": observed,
                "provider_returned_model": provider_returned,
                "model_source": args.get("model_source") or "",
                "provider_response_source": (
                    provider_response_source
                ),
            }

        if any(
            payload.get(key) not in (None, "")
            for key in (
                "model",
                "requested_model",
                "model_alias",
                "resolved_model",
                "served_model",
            )
        ):
            runtime = str(
                payload.get("runtime")
                or payload.get("reviewer_runtime")
                or payload.get("provider")
                or ""
            )
            provider_returned, response_source = _first_present_value(
                ("event_payload.resolved_model", payload.get("resolved_model")),
                ("event_payload.served_model", payload.get("served_model")),
            )
            provider_response_source = (
                _provider_response_reference(payload)
                or _bound_event_reference(event_id, response_source)
            )
            yield {
                "event_id": event_id,
                "gate": gate,
                "lane": str(
                    payload.get("worker_id")
                    or payload.get("reviewer_id")
                    or event.get("kind")
                    or "worker"
                ),
                "runtime": runtime,
                "provider_family": str(payload.get("provider_family") or ""),
                "requested_model": (
                    payload.get("requested_model")
                    or payload.get("model_alias")
                    or payload.get("model")
                ),
                "observed_model": (
                    provider_returned
                    or payload.get("model")
                ),
                "provider_returned_model": provider_returned,
                "model_source": payload.get("model_source") or "",
                "provider_response_source": (
                    provider_response_source
                ),
            }

        for result in _reviewer_results(payload):
            requested = (
                result.get("requested_model")
                or result.get("model_alias")
                or result.get("model")
            )
            provider_returned, response_source = _first_present_value(
                ("reviewer_result.resolved_model", result.get("resolved_model")),
                ("reviewer_result.served_model", result.get("served_model")),
                ("reviewer_result.model", result.get("model")),
            )
            provider_response_source = (
                _provider_response_reference(result)
                or _bound_event_reference(event_id, response_source)
            )
            observed = provider_returned
            if requested in (None, "") and observed in (None, ""):
                continue
            yield {
                "event_id": event_id,
                "gate": gate,
                "lane": str(result.get("reviewer_id") or "reviewer"),
                "runtime": str(
                    result.get("runtime")
                    or result.get("reviewer_runtime")
                    or ""
                ),
                "provider_family": str(result.get("provider_family") or ""),
                "requested_model": requested,
                "observed_model": observed,
                "provider_returned_model": provider_returned,
                "model_source": result.get("model_source") or "",
                "provider_response_source": (
                    provider_response_source
                ),
            }


def _first_present_value(
    *candidates: tuple[str, Any],
) -> tuple[Any, str]:
    for source, value in candidates:
        if value not in (None, ""):
            return value, source
    return None, ""


def _provider_response_reference(payload: dict[str, Any]) -> str:
    for key in (
        "provider_response_receipt_ref",
        "response_receipt_ref",
        "provider_receipt_ref",
        "receipt_ref",
    ):
        reference = _normalise_receipt_reference(payload.get(key))
        if reference:
            return reference
    for key in (
        "provider_response_source",
        "model_provenance",
    ):
        value = str(payload.get(key) or "").strip()
        if _is_bound_reference(value):
            return value
    receipt = payload.get("provider_response_receipt")
    if isinstance(receipt, dict):
        for key in ("receipt_ref", "receipt_id", "id", "ref"):
            reference = _normalise_receipt_reference(receipt.get(key))
            if reference:
                return reference
    return ""


def _normalise_receipt_reference(value: Any) -> str:
    reference = str(value or "").strip()
    if not reference:
        return ""
    if _is_bound_reference(reference):
        return reference
    bound = f"receipt:{reference}"
    return bound if _is_bound_reference(bound) else ""


def _bound_event_reference(event_id: int, source: str) -> str:
    source = str(source or "").strip()
    return f"event:{event_id}:{source}" if event_id > 0 and source else ""


def _resolve_model(
    *,
    requested_model: str,
    observed_model: str,
    provider_returned_model: str,
    provider_response_source: str,
    runtime: str,
    provider_family: str,
) -> tuple[str, str, bool]:
    if (
        provider_returned_model
        and _is_exact_model_identity(provider_returned_model)
        and _is_bound_reference(provider_response_source)
    ):
        return provider_returned_model, "response_model", True
    alias = provider_returned_model or observed_model or requested_model
    if not alias:
        return "", "missing", False
    if _is_exact_model_identity(alias):
        return alias, "configured_model", False
    runtime_key = runtime.lower()
    provider_key = provider_family.lower()
    if "cursor" in runtime_key or provider_key == "cursor":
        return f"cursor:auto/{alias}", "provider_route", False
    if "claude" in runtime_key or provider_key == "anthropic":
        return f"anthropic:claude-code/{alias}", "provider_route", False
    if "litellm" in runtime_key:
        return f"litellm:{alias}", "provider_route", False
    namespace = provider_key or runtime_key or "model-route"
    return f"{namespace}:{alias}", "provider_route", False


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _canonical_component(
    *,
    kind: str,
    component_id: str,
    canonical_bytes: bytes,
    details: dict[str, Any],
    source: str,
    capture_source: str,
    receipt_ref: str,
) -> dict[str, Any]:
    digest = sha256(canonical_bytes).hexdigest()
    return {
        "component_id": component_id,
        "kind": kind,
        "source": source,
        "sha256": digest,
        "details": {
            **details,
            "status": "verified",
            "capture_source": capture_source,
            "receipt_ref": receipt_ref,
            "declared_sha256": digest,
            "computed_sha256": digest,
            "canonical_bytes_base64": base64.b64encode(
                canonical_bytes
            ).decode("ascii"),
            "canonical_size_bytes": len(canonical_bytes),
        },
    }


def _missing_component(
    *,
    kind: str,
    component_id: str,
    status: str,
    source: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "kind": kind,
        "source": source,
        "sha256": "",
        "details": {
            **dict(details or {}),
            "status": status,
            "capture_source": "",
            "receipt_ref": "",
            "declared_sha256": "",
            "computed_sha256": "",
            "canonical_bytes_base64": "",
            "canonical_size_bytes": 0,
        },
    }


def _component_receipt_reference(payload: Mapping[str, Any]) -> str:
    for key in (
        "receipt_ref",
        "artifact_ref",
        "evidence_ref",
        "runtime_receipt_ref",
    ):
        reference = _normalise_receipt_reference(payload.get(key))
        if reference:
            return reference
    for key in ("receipt_id", "id", "ref"):
        reference = _normalise_receipt_reference(payload.get(key))
        if reference:
            return reference
    return ""


def _runtime_component_receipt_index(
    receipts: Iterable[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    indexed: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        category = _COMPONENT_CATEGORY_ALIASES.get(
            str(
                receipt.get("category")
                or receipt.get("kind")
                or ""
            ).strip().lower()
        )
        component_id = str(receipt.get("component_id") or "").strip()
        if not category or not component_id:
            continue
        indexed.setdefault(category, {}).setdefault(
            component_id,
            [],
        ).append(dict(receipt))
    return indexed


def _runtime_component_from_receipts(
    *,
    category: str,
    kind: str,
    component_id: str,
    receipts: list[dict[str, Any]],
    observed_details: dict[str, Any],
    expected_digest: str = "",
) -> dict[str, Any]:
    if not receipts:
        return _missing_component(
            kind=kind,
            component_id=component_id,
            status="not_recorded",
            source="manifest_input",
            details=observed_details,
        )

    candidates: list[dict[str, Any]] = []
    for receipt in receipts:
        declared_sha256 = _normalized_sha256(receipt.get("sha256"))
        receipt_ref = _component_receipt_reference(receipt)
        capture_source = str(receipt.get("capture_source") or "").strip()
        canonical_bytes, bytes_status = _canonical_contract_bytes(receipt)
        digest_only_receipt = (
            bytes_status == "canonical_bytes_missing"
            and bool(declared_sha256)
        )
        computed_sha256 = (
            sha256(canonical_bytes).hexdigest()
            if canonical_bytes is not None
            else declared_sha256 if digest_only_receipt else ""
        )
        if not declared_sha256:
            status = "digest_missing"
        elif not receipt_ref:
            status = "receipt_unbound"
        elif capture_source != "execution_time":
            status = "capture_source_invalid"
        elif (
            canonical_bytes is None
            and not digest_only_receipt
        ):
            status = bytes_status
        elif computed_sha256 != declared_sha256:
            status = "digest_mismatch"
        elif expected_digest and declared_sha256 != expected_digest:
            status = "observed_digest_mismatch"
        else:
            status = "verified"
        candidates.append({
            "component_id": component_id,
            "kind": kind,
            "source": str(
                receipt.get("source") or "runtime_component_receipt"
            ),
            "sha256": computed_sha256,
            "details": {
                **observed_details,
                "status": status,
                "capture_source": capture_source,
                "receipt_ref": receipt_ref,
                "declared_sha256": declared_sha256,
                "computed_sha256": computed_sha256,
                "canonical_bytes_base64": (
                    base64.b64encode(canonical_bytes).decode("ascii")
                    if canonical_bytes is not None
                    else ""
                ),
                "canonical_size_bytes": (
                    len(canonical_bytes)
                    if canonical_bytes is not None
                    else 0
                ),
                "digest_only": digest_only_receipt,
                "expected_digest": expected_digest,
            },
        })

    verified = [
        candidate
        for candidate in candidates
        if candidate["details"]["status"] == "verified"
    ]
    verified_hashes = {
        str(candidate.get("sha256") or "")
        for candidate in verified
    }
    if len(verified_hashes) == 1 and len(verified) == len(candidates):
        return verified[0]
    if len(candidates) > 1:
        chosen = candidates[0]
        chosen["details"]["status"] = "conflicting_artifacts"
        return chosen
    return candidates[0]


def _verified_component_artifact(
    category: str,
    component: Any,
) -> bool:
    if category == "tool_contracts":
        return _verified_tool_contract_component(component)
    if not isinstance(component, dict):
        return False
    details = component.get("details")
    if not isinstance(details, dict):
        return False
    component_sha256 = _normalized_sha256(component.get("sha256"))
    declared_sha256 = _normalized_sha256(details.get("declared_sha256"))
    computed_sha256 = _normalized_sha256(details.get("computed_sha256"))
    if (
        details.get("status") != "verified"
        or not component_sha256
        or component_sha256 != declared_sha256
        or component_sha256 != computed_sha256
        or not _is_bound_reference(details.get("receipt_ref"))
    ):
        return False
    capture_source = str(details.get("capture_source") or "")
    if category == "prompts":
        if capture_source not in {
            "event_ledger",
            "accepted_gate_event",
            "execution_time",
        }:
            return False
    elif capture_source != "execution_time":
        return False

    encoded = details.get("canonical_bytes_base64")
    if isinstance(encoded, str) and encoded:
        try:
            canonical_bytes = base64.b64decode(
                encoded.encode("ascii"),
                validate=True,
            )
            canonical_size_bytes = int(
                details.get("canonical_size_bytes")
            )
        except (
            binascii.Error,
            TypeError,
            ValueError,
            UnicodeEncodeError,
        ):
            return False
        return (
            canonical_size_bytes == len(canonical_bytes)
            and sha256(canonical_bytes).hexdigest() == component_sha256
        )
    return (
        details.get("digest_only") is True
    )


def _prompt_components(
    events: list[dict[str, Any]],
    handoff_packets: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_id = int(event.get("event_id") or 0)
        message_type = str(payload.get("message_type") or "").lower()
        content = payload.get("content")
        if isinstance(content, str) and content and (
            "request" in message_type
            or "prompt" in message_type
            or event.get("kind") == "dual_agent_gate_request"
        ):
            canonical_bytes = _canonical_json_bytes({
                "message_type": message_type,
                "content": content,
            })
            components.append(_canonical_component(
                kind="prompt",
                component_id=f"event:{event_id}",
                canonical_bytes=canonical_bytes,
                details={
                    "message_type": message_type,
                    "content": content,
                },
                source="event_ledger",
                capture_source="event_ledger",
                receipt_ref=f"event:{event_id}:payload.content",
            ))
        for key in ("prompt", "instruction", "system_prompt", "user_prompt"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                components.append(_canonical_component(
                    kind="prompt",
                    component_id=f"event:{event_id}:{key}",
                    canonical_bytes=_canonical_json_bytes({key: value}),
                    details={key: value},
                    source="event_ledger",
                    capture_source="event_ledger",
                    receipt_ref=f"event:{event_id}:payload.{key}",
                ))
    for index, packet in enumerate(handoff_packets):
        content = packet.get("content")
        if isinstance(content, str) and content:
            component_id = f"handoff:{index}:{packet.get('path') or ''}"
            content_bytes = content.encode("utf-8")
            if (
                packet.get("capture_source") == "accepted_gate_event"
                and packet.get("status") == "captured"
                and _normalized_sha256(packet.get("sha256"))
                == sha256(content_bytes).hexdigest()
            ):
                components.append(_canonical_component(
                    kind="prompt",
                    component_id=component_id,
                    canonical_bytes=content_bytes,
                    details={"content": content},
                    source="handoff_packet",
                    capture_source="accepted_gate_event",
                    receipt_ref=(
                        f"artifact:{packet.get('path') or component_id}"
                    ),
                ))
            else:
                components.append(_missing_component(
                    kind="prompt",
                    component_id=component_id,
                    status="not_acceptance_bound",
                    source="handoff_packet",
                    details={
                        "path": packet.get("path"),
                        "capture_source": packet.get("capture_source"),
                        "claimed_sha256": packet.get("sha256"),
                    },
                ))
    return _dedupe_components(components) or [
        _missing_component(
            kind="prompt",
            component_id="prompt:not-recorded",
            status="not_recorded",
            source="manifest_fallback",
        )
    ]


def _tool_contract_components(
    events: list[dict[str, Any]],
    canonical_tool_contracts: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    required = sorted({
        str(call.get("name") or "unknown-tool")
        for event in events
        for call in _tool_calls(
            event.get("payload")
            if isinstance(event.get("payload"), dict)
            else {}
        )
    })
    artifacts_by_name: dict[str, list[dict[str, Any]]] = {}
    invalid: set[str] = set()
    for index, artifact in enumerate(canonical_tool_contracts):
        if not isinstance(artifact, dict):
            invalid.add(f"<invalid:{index}>")
            continue
        name = str(artifact.get("tool_name") or artifact.get("name") or "").strip()
        if not name:
            invalid.add(f"<unnamed:{index}>")
            continue
        artifacts_by_name.setdefault(name, []).append(artifact)

    components: list[dict[str, Any]] = []
    verified_names: set[str] = set()
    for name, artifacts in sorted(artifacts_by_name.items()):
        records = [
            _tool_contract_artifact_component(name, artifact)
            for artifact in artifacts
        ]
        verified_hashes = {
            str(record.get("sha256") or "")
            for record in records
            if (
                isinstance(record.get("details"), dict)
                and record["details"].get("status") == "verified"
            )
        }
        all_verified = bool(records) and all(
            isinstance(record.get("details"), dict)
            and record["details"].get("status") == "verified"
            and record.get("sha256") in verified_hashes
            for record in records
        )
        if len(verified_hashes) == 1 and all_verified:
            verified_names.add(name)
        else:
            invalid.add(name)
            if len(verified_hashes) > 1:
                for record in records:
                    record["details"]["status"] = "conflicting_artifacts"
        components.extend(records)

    missing = sorted(set(required) - verified_names)
    for name in missing:
        if name in invalid:
            continue
        components.append(_missing_component(
            kind="tool_contract",
            component_id=f"tool-contract:{name}",
            status="not_recorded",
            details={
                "tool_name": name,
            },
            source="manifest_input",
        ))
    if not required and not components:
        components.append(_missing_component(
            kind="tool_contract",
            component_id="tool-contract:none",
            status="not_used",
            source="manifest_fallback",
        ))
    return (
        _dedupe_tool_contract_components(components),
        required,
        missing,
        sorted(invalid),
    )


def _tool_contract_artifact_component(
    name: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    canonical_bytes, bytes_status = _canonical_contract_bytes(artifact)
    declared_sha256 = _normalized_sha256(artifact.get("sha256"))
    computed_sha256 = (
        sha256(canonical_bytes).hexdigest()
        if canonical_bytes is not None
        else ""
    )
    receipt_ref = _component_receipt_reference(artifact)
    capture_source = str(artifact.get("capture_source") or "").strip()
    if bytes_status:
        status = bytes_status
    elif not declared_sha256:
        status = "digest_missing"
    elif declared_sha256 != computed_sha256:
        status = "digest_mismatch"
    elif not receipt_ref:
        status = "receipt_unbound"
    elif capture_source != "execution_time":
        status = "capture_source_invalid"
    else:
        status = "verified"
    details = {
        "status": status,
        "tool_name": name,
        "declared_sha256": declared_sha256,
        "computed_sha256": computed_sha256,
        "canonical_bytes_base64": (
            base64.b64encode(canonical_bytes).decode("ascii")
            if canonical_bytes is not None
            else ""
        ),
        "canonical_size_bytes": (
            len(canonical_bytes)
            if canonical_bytes is not None
            else 0
        ),
        "media_type": str(
            artifact.get("media_type") or "application/json"
        ),
        "schema_version": str(artifact.get("schema_version") or ""),
        "capture_source": capture_source,
        "receipt_ref": receipt_ref,
    }
    return {
        "component_id": f"tool-contract:{name}",
        "kind": "tool_contract",
        "source": str(artifact.get("source") or "manifest_input"),
        "sha256": computed_sha256,
        "details": details,
    }


def _canonical_contract_bytes(
    artifact: dict[str, Any],
) -> tuple[bytes | None, str]:
    has_bytes = "canonical_bytes" in artifact
    has_base64 = "canonical_bytes_base64" in artifact
    if has_bytes and has_base64:
        return None, "canonical_bytes_ambiguous"
    if has_bytes:
        value = artifact.get("canonical_bytes")
        if isinstance(value, bytes):
            return value, ""
        if isinstance(value, bytearray):
            return bytes(value), ""
        if isinstance(value, str):
            return value.encode("utf-8"), ""
        return None, "canonical_bytes_invalid"
    if has_base64:
        encoded = artifact.get("canonical_bytes_base64")
        if not isinstance(encoded, str):
            return None, "canonical_bytes_invalid"
        try:
            return base64.b64decode(encoded.encode("ascii"), validate=True), ""
        except (binascii.Error, ValueError, UnicodeEncodeError):
            return None, "canonical_bytes_invalid"
    return None, "canonical_bytes_missing"


def _dedupe_tool_contract_components(
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_identity = {
        (
            str(item.get("component_id") or ""),
            str(item.get("sha256") or ""),
            str(
                item.get("details", {}).get("status")
                if isinstance(item.get("details"), dict)
                else ""
            ),
        ): item
        for item in components
    }
    return sorted(
        by_identity.values(),
        key=lambda item: (
            str(item.get("component_id") or ""),
            str(item.get("sha256") or ""),
        ),
    )


def _verified_tool_contract_component(component: Any) -> bool:
    if not isinstance(component, dict):
        return False
    details = component.get("details")
    if not isinstance(details, dict):
        return False
    if (
        component.get("kind") != "tool_contract"
        or details.get("status") != "verified"
        or not str(details.get("tool_name") or "").strip()
        or details.get("capture_source") != "execution_time"
        or not _is_bound_reference(details.get("receipt_ref"))
    ):
        return False
    component_sha256 = _normalized_sha256(component.get("sha256"))
    declared_sha256 = _normalized_sha256(details.get("declared_sha256"))
    computed_sha256 = _normalized_sha256(details.get("computed_sha256"))
    if (
        not component_sha256
        or declared_sha256 != component_sha256
        or computed_sha256 != component_sha256
    ):
        return False
    encoded = details.get("canonical_bytes_base64")
    if not isinstance(encoded, str) or not encoded:
        return False
    try:
        canonical_bytes = base64.b64decode(
            encoded.encode("ascii"),
            validate=True,
        )
        canonical_size_bytes = int(details.get("canonical_size_bytes"))
    except (
        binascii.Error,
        TypeError,
        ValueError,
        UnicodeEncodeError,
    ):
        return False
    return (
        canonical_size_bytes == len(canonical_bytes)
        and sha256(canonical_bytes).hexdigest() == component_sha256
    )


def _container_components(
    events: list[dict[str, Any]],
    receipt_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for path, value in _find_keys(
            payload,
            {"container", "container_digest", "container_image", "image_digest"},
        ):
            component_id = f"container:{path}"
            components.append(_runtime_component_from_receipts(
                category="containers",
                kind="container",
                component_id=component_id,
                receipts=receipt_index.get("containers", {}).get(
                    component_id,
                    [],
                ),
                observed_details={
                    "path": path,
                    "value": value,
                    "claimed_digest": _normalized_sha256(value),
                },
                expected_digest=_normalized_sha256(value),
            ))
    return _dedupe_components(components) or [
        _missing_component(
            kind="container",
            component_id="container:host-runtime",
            status="not_used",
            source="manifest_fallback",
            details={"runtime": "host"},
        )
    ]


def _cli_components(
    events: list[dict[str, Any]],
    receipt_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for call in _tool_calls(payload):
            name = str(call.get("name") or "")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            runtime = str(args.get("runtime") or call.get("runtime") or _runtime_for_tool(name))
            if not name.startswith("invoke_") and not runtime:
                continue
            cli_command = str(
                args.get("cli_command")
                or _cli_for_runtime(runtime, name)
            )
            descriptor: dict[str, Any] = {
                "tool": name,
                "runtime": runtime,
                "cli_command": cli_command,
                "requested_model": args.get("requested_model") or args.get("model"),
                "claimed_sha256": _normalized_sha256(
                    args.get("executable_sha256")
                    or args.get("cli_sha256")
                    or call.get("executable_sha256")
                    or call.get("cli_sha256")
                ),
            }
            component_id = f"cli:{name or runtime or cli_command}"
            components.append(_runtime_component_from_receipts(
                category="cli",
                kind="cli",
                component_id=component_id,
                receipts=receipt_index.get("cli", {}).get(component_id, []),
                observed_details=descriptor,
            ))
    return _dedupe_components(components) or [
        _missing_component(
            kind="cli",
            component_id="cli:none",
            status="not_used",
            source="manifest_fallback",
        )
    ]


def _evaluator_components(
    events: list[dict[str, Any]],
    receipt_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for call in _tool_calls(payload):
            name = str(call.get("name") or "")
            if not name.startswith(("evaluate_", "verify_", "check_")):
                continue
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            claimed_hash = _normalized_sha256(
                args.get("verifier_hash")
                or args.get("evaluator_hash")
                or args.get("evaluator_sha256")
                or call.get("verifier_hash")
                or call.get("evaluator_hash")
            )
            component_id = f"evaluator:{name}"
            components.append(_runtime_component_from_receipts(
                category="evaluators",
                kind="evaluator",
                component_id=component_id,
                receipts=receipt_index.get("evaluators", {}).get(
                    component_id,
                    [],
                ),
                observed_details={
                    "name": name,
                    "probe_id": call.get("probe_id") or args.get("probe_id"),
                    "argument_fields": sorted(str(key) for key in args),
                    "claimed_sha256": claimed_hash,
                },
            ))
    return _dedupe_components(components) or [
        _missing_component(
            kind="evaluator",
            component_id="evaluator:none",
            status="not_used",
            source="manifest_fallback",
        )
    ]


def _tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    trace = (
        payload.get("trace_envelope")
        if isinstance(payload.get("trace_envelope"), dict)
        else {}
    )
    values = trace.get("tool_calls")
    if not isinstance(values, list):
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        values = metadata.get("tool_calls")
    return [item for item in (values or []) if isinstance(item, dict)]


def _reviewer_results(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for key in ("independent_reviewer_results", "reviewer_results"):
        values = payload.get(key)
        if isinstance(values, list):
            yield from (item for item in values if isinstance(item, dict))
    nested = payload.get("cursor_review")
    if isinstance(nested, dict):
        yield nested


def _runtime_for_tool(name: str) -> str:
    lowered = name.lower()
    if "cursor" in lowered:
        return "cursor_sdk"
    if "codex" in lowered:
        return "codex_cli"
    if "claude" in lowered:
        return "claude_code"
    if "litellm" in lowered:
        return "litellm_structured"
    return ""


def _cli_for_runtime(runtime: str, name: str) -> str:
    lowered = f"{runtime} {name}".lower()
    if "claude" in lowered:
        return "claude"
    if "codex" in lowered:
        return "codex"
    if "cursor" in lowered:
        return "cursor-agent"
    return ""


def _provider_for(runtime: str, model: str) -> str:
    lowered = f"{runtime} {model}".lower()
    if "claude" in lowered or "anthropic" in lowered:
        return "anthropic"
    if "cursor" in lowered:
        return "cursor"
    if "gemini" in lowered or "google" in lowered:
        return "google"
    if "codex" in lowered or "gpt" in lowered or "openai" in lowered:
        return "openai"
    if "litellm" in lowered:
        return "openai_compatible"
    return "unknown"


def _find_keys(value: Any, keys: set[str], prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key) in keys and item not in (None, "", [], {}):
                yield path, item
            yield from _find_keys(item, keys, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _find_keys(item, keys, f"{prefix}[{index}]")


def _git_changed_paths(root: Path) -> list[Path] | None:
    commands = (
        ("diff", "--name-only", "-z", "HEAD"),
        ("diff", "--cached", "--name-only", "-z", "HEAD"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    paths: set[Path] = set()
    for args in commands:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=root,
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        paths.update(
            Path(raw.decode("utf-8", errors="replace"))
            for raw in completed.stdout.split(b"\0")
            if raw
        )
    return sorted(paths, key=lambda path: path.as_posix())


def _git_output(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _workspace_content_sha256(
    root: Path,
    *,
    head: str,
    immutable_snapshot: dict[str, Any],
) -> str:
    base_tree = (
        _git_output(root, "rev-parse", f"{head}^{{tree}}")
        if head
        else ""
    )
    return _canonical_sha256({
        "base_commit": head,
        "base_tree": base_tree,
        "overlay_sha256": immutable_snapshot.get("sha256"),
    })


def _source_artifact_hashes(
    root: Path,
    handoff: dict[str, Any],
) -> dict[str, str]:
    artifacts = handoff.get("planning_artifacts")
    if not isinstance(artifacts, list):
        return {}
    root_resolved = root.expanduser().resolve()
    hashes: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        kind = str(artifact.get("kind") or "").strip()
        path_text = str(artifact.get("path") or "").strip()
        if not kind or not path_text:
            continue
        candidate = Path(path_text).expanduser()
        if not candidate.is_absolute():
            candidate = root_resolved / candidate
        try:
            path = candidate.resolve()
            path.relative_to(root_resolved)
        except ValueError:
            raise ValueError(
                "planning artifact path escapes workspace root: "
                f"{path_text!r}"
            ) from None
        except OSError:
            continue
        if (
            path.is_file()
            and not _excluded_snapshot_path(path, root_resolved)
        ):
            hashes[kind] = sha256(path.read_bytes()).hexdigest()
        elif _is_sha256(artifact.get("sha256")):
            hashes[kind] = str(artifact["sha256"]).lower().removeprefix("sha256:")
    return hashes


def _source_artifact_paths(
    root: Path,
    handoff: Mapping[str, Any],
) -> tuple[Path, ...]:
    artifacts = handoff.get("planning_artifacts")
    if not isinstance(artifacts, list):
        return ()
    paths = [
        str(artifact.get("path") or "").strip()
        for artifact in artifacts
        if isinstance(artifact, Mapping)
        and str(artifact.get("path") or "").strip()
    ]
    return _normalize_relative_exclusions(root, paths)


def _workspace_overlay_entry(path: Path, *, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    if path.is_symlink():
        return {
            "path": relative,
            "kind": "symlink",
            "target": path.readlink().as_posix(),
        }
    try:
        data = path.read_bytes()
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        return {"path": relative, "kind": "deleted"}
    entry: dict[str, Any] = {
        "path": relative,
        "kind": "file",
        "mode": mode,
        "size": len(data),
        "sha256": sha256(data).hexdigest(),
    }
    if len(data) > _WORKSPACE_OVERLAY_MAX_CONTENT_BYTES:
        entry["content_omitted"] = "size_limit_exceeded"
        entry["content_limit_bytes"] = _WORKSPACE_OVERLAY_MAX_CONTENT_BYTES
    else:
        entry["content_base64"] = base64.b64encode(data).decode("ascii")
    return entry


def _excluded_snapshot_path(
    path: Path,
    root: Path,
    *,
    excluded_roots: tuple[Path, ...] = (),
    included_paths: tuple[Path, ...] = (),
) -> bool:
    relative = path.relative_to(root)
    explicitly_included = relative in included_paths
    if any(
        relative == excluded or excluded in relative.parents
        for excluded in excluded_roots
    ) and not explicitly_included:
        return True
    parts = set(relative.parts)
    if parts & {
        ".git",
        ".venv",
        ".claude",
        ".codex",
        ".codex-supervisor",
        ".cortex",
        ".handoff",
        ".orchestrator-state",
        ".scratch",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }:
        return True
    name = path.name.lower()
    if _is_runtime_state_snapshot_path(relative):
        return True
    if name.startswith(".env") or name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return any(token in name for token in ("secret", "credential", "token"))


def _is_runtime_state_snapshot_path(relative: Path) -> bool:
    name = relative.name.lower()
    base_name = name
    for suffix in _RUNTIME_STATE_SIDECAR_SUFFIXES:
        if base_name.endswith(suffix):
            base_name = base_name[: -len(suffix)]
            break
    runtime_name = (
        base_name in _RUNTIME_STATE_FILENAMES
        or any(
            base_name.endswith(f"-state{suffix}")
            for suffix in _RUNTIME_STATE_SUFFIXES
        )
    )
    if not runtime_name:
        return False
    return (
        len(relative.parts) == 1
        or any(
            part.lower() in _RUNTIME_STATE_DIRECTORIES
            for part in relative.parts[:-1]
        )
    )


def _normalize_relative_exclusions(
    root: Path,
    excluded_roots: Iterable[str | Path],
) -> tuple[Path, ...]:
    root_resolved = root.expanduser().resolve()
    normalized: set[Path] = set()
    for raw in excluded_roots:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root_resolved / candidate
        try:
            relative = candidate.resolve().relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if relative.parts:
            normalized.add(relative)
    return tuple(sorted(normalized, key=lambda item: item.as_posix()))


def _dedupe_components(
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_identity = {
        (
            str(item.get("component_id") or ""),
            str(item.get("sha256") or ""),
            str(
                item.get("details", {}).get("status")
                if isinstance(item.get("details"), dict)
                else ""
            ),
            str(
                item.get("details", {}).get("receipt_ref")
                if isinstance(item.get("details"), dict)
                else ""
            ),
        ): item
        for item in components
    }
    return sorted(
        by_identity.values(),
        key=lambda item: (str(item["component_id"]), str(item["sha256"])),
    )


def _resolution_rank(record: dict[str, Any]) -> tuple[int, int]:
    source_rank = {
        "response_model": 4,
        "configured_model": 2,
        "provider_route": 1,
        "missing": 0,
    }
    return (
        1 if record.get("exact_model_identity") else 0,
        source_rank.get(str(record.get("resolution_source") or ""), 0),
    )


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}:{_canonical_sha256(payload)[:24]}"


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def _normalized_sha256(value: Any) -> str:
    raw = str(value or "").strip().lower().removeprefix("sha256:")
    return raw if _is_sha256(raw) else ""


def _is_exact_model_identity(value: Any) -> bool:
    normalized = str(value or "").strip().casefold()
    if not normalized or normalized in _MODEL_ALIASES:
        return False
    route_tail = normalized.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return route_tail not in _MODEL_ALIASES


def _is_bound_reference(value: Any) -> bool:
    reference = str(value or "").strip()
    return any(
        reference.startswith(prefix) and len(reference) > len(prefix)
        for prefix in _BOUND_REFERENCE_PREFIXES
    )


def _is_sha256(value: Any) -> bool:
    raw = str(value or "").strip().lower().removeprefix("sha256:")
    return len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw)


def _is_git_commit(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    return len(raw) in {40, 64} and all(ch in "0123456789abcdef" for ch in raw)
