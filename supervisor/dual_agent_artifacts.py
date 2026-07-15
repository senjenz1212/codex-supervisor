"""Markdown artifacts derived from dual-agent gate ledger events."""
from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .evidence_ledger import (
    canonical_json_bytes,
    canonical_json_text,
    strict_json_object_loads,
    verify_event_chain,
    verify_event_chain_structure,
)
from .failure_taxonomy import (
    FAILURE_TAXONOMY_VERSION,
    detect_sequence_failures,
    mast_coverage_matrix,
)
from .grade_revisions import (
    DecisionGradeCitation,
    GradeBook,
    RunEnvelopeRef,
)
from .production_trace import (
    PRODUCTION_TRACE_RECEIPT_SCHEMA_VERSION,
    ProductionTraceEvidence,
)
from .redaction import redact
from .review_packets import build_review_packet
from .run_manifest import (
    EXECUTION_PROVENANCE_SCHEMA_VERSION,
    build_execution_provenance,
    build_workspace_overlay,
    execution_provenance_issues,
)
from .state import State
from .trace_graph import (
    NodeType,
    TraceClosureBinding,
    TraceGraphStore,
    canonical_revision_hash,
)
from .trace_envelope import TRACE_ENVELOPE_SCHEMA_VERSION, ensure_tool_call_timing

_RUNTIME_STATE_FILENAMES = frozenset({
    "experiments.db",
    "grades.db",
    "state.db",
    "state.sqlite",
    "state.sqlite3",
    "trace.db",
})
_RUNTIME_STATE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_RUNTIME_STATE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")
_RUNTIME_STATE_DIRECTORIES = frozenset({
    ".codex-supervisor",
    ".orchestrator-state",
})
_NON_PRESERVABLE_EXPORT_PATHS = frozenset({
    "index.md",
    "triage.md",
    "prd.md",
    "tdd.md",
    "grill-findings.md",
    "issues.md",
    "screenshots.md",
    "interactions.md",
    "transcript.md",
    "transcript.jsonl",
    "mast-coverage.md",
    "replay/manifest.json",
    "replay/workspace-snapshot.json",
    "replay/mast-coverage.json",
    "replay/evidence-ledger.jsonl",
    "replay/export-integrity.json",
})
_OUTPUT_RELATIVE_ARTIFACT_ROOTS = frozenset({
    "artifacts",
    "comparison",
    "pilot",
    "report",
    "reports",
    "replay",
    "results",
    "source",
})
_LEGACY_SOURCE_ARTIFACT_FILENAMES = frozenset({
    "grill-findings-tdd.md",
    "grill-findings.md",
    "implementation-plan.md",
    "issues.md",
    "prd.md",
    "tdd.md",
})


@dataclass(frozen=True)
class DualAgentArtifactExport:
    status: str
    output_dir: Path
    files: tuple[Path, ...]
    export_root_sha256: str | None = None
    ledger_head_hash: str | None = None
    ledger_authoritative: bool | None = None


@dataclass(frozen=True)
class DualAgentExportVerification:
    valid: bool
    package_dir: Path
    issues: tuple[str, ...]
    export_root_sha256: str | None = None
    ledger_head_hash: str | None = None


@dataclass(frozen=True)
class ScreenshotArtifact:
    path: str | Path
    label: str
    note: str = ""
    source: str = ""
    validation_status: str = ""
    validation_notes: str = ""


@dataclass(frozen=True)
class _BoundArtifactRef:
    source_event_id: int
    source_kind: str
    source_path: str
    sha256: str


@dataclass(frozen=True)
class _CanonicalProductionTraceExpectation:
    evidence: ProductionTraceEvidence
    trace_store_path: Path
    gradebook_path: Path


def verify_dual_agent_export(
    package_dir: str | Path,
    *,
    expected_root: str | None = None,
    expected_ledger_head: str | None = None,
) -> DualAgentExportVerification:
    """Verify a copied public export without trusting its source workspace.

    ``expected_root`` and ``expected_ledger_head`` are caller-owned pins.  The
    package can prove internal consistency by itself, but only an external pin
    makes substitution or truncation detectable outside the package.
    """

    package = Path(package_dir).expanduser().absolute()
    issues: list[str] = []
    export_root: str | None = None
    ledger_head: str | None = None
    try:
        _reject_symlink_components(package)
        if not package.is_dir() or package.is_symlink():
            raise ValueError("export package is not a regular directory")
        _reject_tree_symlinks(package)

        integrity_relative = "replay/export-integrity.json"
        integrity_bytes = _read_export_regular_file(
            package,
            integrity_relative,
        )
        integrity = strict_json_object_loads(integrity_bytes)
        if (
            integrity.get("schema_version")
            != "dual-agent-public-export-integrity/v1"
        ):
            issues.append("unsupported export integrity schema")
        if integrity.get("hash_algorithm") != "sha256":
            issues.append("export integrity hash algorithm is not sha256")
        if integrity.get("integrity_path") != integrity_relative:
            issues.append("export integrity path is not canonical")

        export_root = _canonical_sha256_or_none(
            integrity.get("export_root_sha256")
        )
        if export_root is None:
            issues.append("export root is not a canonical sha256")
        root_preimage = dict(integrity)
        root_preimage.pop("export_root_sha256", None)
        computed_root = sha256(
            canonical_json_bytes(root_preimage)
        ).hexdigest()
        if export_root is not None and computed_root != export_root:
            issues.append("export root differs from the integrity document")
        if expected_root is not None:
            normalized_expected_root = _canonical_sha256_or_none(
                expected_root
            )
            if normalized_expected_root is None:
                issues.append("expected export root is not a canonical sha256")
            elif export_root != normalized_expected_root:
                issues.append(
                    "expected export root differs from the package root"
                )

        descriptors = _export_file_descriptors(integrity, issues=issues)
        described_paths = [item["path"] for item in descriptors]
        actual_paths = sorted(
            path.relative_to(package).as_posix()
            for path in _export_tree_files(package)
            if path.relative_to(package).as_posix() != integrity_relative
        )
        if described_paths != actual_paths:
            issues.append(
                "export file descriptors do not match the exact package tree"
            )

        file_bytes: dict[str, bytes] = {}
        for descriptor in descriptors:
            relative = descriptor["path"]
            try:
                content = _read_export_regular_file(package, relative)
            except (OSError, TypeError, ValueError) as exc:
                issues.append(
                    f"export file {relative!r} cannot be read safely: {exc}"
                )
                continue
            file_bytes[relative] = content
            if len(content) != descriptor["size"]:
                issues.append(
                    f"export file {relative!r} size differs from descriptor"
                )
            if sha256(content).hexdigest() != descriptor["sha256"]:
                issues.append(
                    f"export file {relative!r} sha256 differs from descriptor"
                )

        file_tree = {
            "schema_version": "dual-agent-public-export-file-tree/v1",
            "files": descriptors,
        }
        if (
            integrity.get("file_tree_schema_version")
            != file_tree["schema_version"]
        ):
            issues.append("export file-tree schema is not canonical")
        if (
            integrity.get("file_tree_scope")
            != "explicit_generated_files_except_integrity_document"
        ):
            issues.append("export file-tree scope is not canonical")
        expected_file_tree_hash = _canonical_sha256_or_none(
            integrity.get("file_tree_sha256")
        )
        computed_file_tree_hash = sha256(
            canonical_json_bytes(file_tree)
        ).hexdigest()
        if (
            expected_file_tree_hash is None
            or expected_file_tree_hash != computed_file_tree_hash
        ):
            issues.append("export file-tree hash is invalid")

        raw_ledger = integrity.get("ledger")
        if not isinstance(raw_ledger, Mapping):
            issues.append("export ledger manifest is not an object")
        else:
            ledger_manifest = dict(raw_ledger)
            ledger_head = _canonical_sha256_or_none(
                ledger_manifest.get("head_event_hash")
            )
            ledger_path = _canonical_export_relative_path(
                ledger_manifest.get("path")
            )
            ledger_content = (
                file_bytes.get(ledger_path)
                if ledger_path is not None
                else None
            )
            if ledger_path is None:
                issues.append("export ledger path is not canonical")
            elif ledger_content is None:
                issues.append("export ledger file is absent")
            else:
                ledger_rows = _strict_canonical_jsonl(
                    ledger_content,
                    label="export ledger",
                    issues=issues,
                )
                ledger_sha256 = _canonical_sha256_or_none(
                    ledger_manifest.get("sha256")
                )
                if (
                    ledger_sha256 is None
                    or sha256(ledger_content).hexdigest() != ledger_sha256
                ):
                    issues.append("export ledger sha256 is invalid")
                run_id = str(ledger_manifest.get("run_id") or "").strip()
                identity_head = _canonical_sha256_or_none(
                    ledger_manifest.get("head_event_identity_hash")
                )
                if not run_id:
                    issues.append("export ledger run_id is empty")
                if ledger_head is None:
                    issues.append(
                        "export ledger head is not a canonical sha256"
                    )
                if identity_head is None:
                    issues.append(
                        "export ledger identity head is not canonical"
                    )
                if (
                    run_id
                    and ledger_head is not None
                    and identity_head is not None
                ):
                    verification = verify_event_chain(
                        ledger_rows,
                        expected_run_id=run_id,
                        expected_head_hash=ledger_head,
                        expected_event_identity_hash=identity_head,
                    )
                    if not verification.valid:
                        issues.append(
                            "export ledger verification failed: "
                            f"{verification.failure_code}: "
                            f"{verification.detail}"
                        )
                    _compare_export_ledger_manifest(
                        ledger_manifest,
                        verification=verification,
                        issues=issues,
                    )
                    _verify_export_replay_manifest(
                        package,
                        file_bytes=file_bytes,
                        integrity_ledger=ledger_manifest,
                        ledger_rows=ledger_rows,
                        issues=issues,
                    )
            if expected_ledger_head is not None:
                normalized_expected_head = _canonical_sha256_or_none(
                    expected_ledger_head
                )
                if normalized_expected_head is None:
                    issues.append(
                        "expected ledger head is not a canonical sha256"
                    )
                elif ledger_head != normalized_expected_head:
                    issues.append(
                        "expected ledger head differs from the package ledger"
                    )
    except (OSError, TypeError, ValueError) as exc:
        issues.append(f"export package verification failed: {exc}")

    return DualAgentExportVerification(
        valid=not issues,
        package_dir=package,
        issues=tuple(dict.fromkeys(issues)),
        export_root_sha256=export_root,
        ledger_head_hash=ledger_head,
    )


def export_dual_agent_run_artifacts(
    state: State,
    *,
    run_id: str,
    task_id: str,
    output_dir: str | Path,
    screenshots: tuple[ScreenshotArtifact, ...] = (),
    require_complete_provenance: bool = False,
    require_complete_trace: bool = False,
    require_authoritative_ledger: bool = False,
    trusted_workspace_root: str | Path | None = None,
    provider_model_resolutions: Iterable[dict[str, Any]] = (),
    canonical_tool_contracts: Iterable[dict[str, Any]] = (),
    runtime_component_receipts: Iterable[dict[str, Any]] = (),
) -> DualAgentArtifactExport:
    out_dir = Path(output_dir)
    ledger_rows, captured_head_event_id = _read_run_ledger_rows(
        state,
        run_id=run_id,
    )
    ledger_rows = [
        {**row, "payload": redact(row.get("payload"))}
        for row in ledger_rows
    ]
    events = _task_events_from_ledger_rows(
        ledger_rows,
        task_id=task_id,
    )
    if not events:
        return DualAgentArtifactExport(
            status="not_found",
            output_dir=out_dir,
            files=(),
        )
    _prepare_export_destination(out_dir)
    workspace_snapshot = _workspace_snapshot_manifest(
        events,
        output_dir=out_dir,
        trusted_workspace_root=trusted_workspace_root,
    )
    staging_dir = Path(tempfile.mkdtemp(
        prefix=f".{out_dir.name}.export-",
        dir=out_dir.parent,
    ))
    by_gate = _events_by_gate(events)
    try:
        (
            preserved_artifact_files,
            preserved_artifact_manifest,
            unresolved_artifact_manifest,
        ) = _copy_hash_bound_export_artifacts(
            events,
            out_dir,
            staging_dir,
            trusted_workspace_root=trusted_workspace_root,
        )
        files = (
            staging_dir / "index.md",
            staging_dir / "triage.md",
            staging_dir / "prd.md",
            staging_dir / "tdd.md",
            staging_dir / "grill-findings.md",
            staging_dir / "issues.md",
            staging_dir / "screenshots.md",
            staging_dir / "outcome-review.md",
            staging_dir / "interactions.md",
            staging_dir / "transcript.md",
            staging_dir / "transcript.jsonl",
            staging_dir / "mast-coverage.md",
            staging_dir / "replay" / "manifest.json",
            staging_dir / "replay" / "workspace-snapshot.json",
            staging_dir / "replay" / "mast-coverage.json",
            staging_dir / "replay" / "evidence-ledger.jsonl",
        )
        screenshot_files = _copy_screenshots(
            staging_dir,
            screenshots,
            trusted_workspace_root=trusted_workspace_root,
        )
        files[12].parent.mkdir(parents=True, exist_ok=True)
        transcript_jsonl = _transcript_jsonl(events)
        production_trace_manifest, production_trace_files = (
            _export_production_trace_records(
                events,
                ledger_rows=ledger_rows,
                output_dir=staging_dir,
                trusted_workspace_root=trusted_workspace_root,
            )
        )
        ledger_manifest, ledger_text = _ledger_export_manifest(
            ledger_rows,
            run_id=run_id,
            captured_head_event_id=captured_head_event_id,
            path=files[15].relative_to(staging_dir).as_posix(),
            authoritative_verification=state.verify_event_ledger(run_id),
        )
        files[15].write_text(ledger_text, encoding="utf-8")
        mast_coverage = mast_coverage_matrix(events)
        files[0].write_text(
            _index_markdown(run_id, task_id, by_gate),
            encoding="utf-8",
        )
        files[1].write_text(
            _triage_markdown(run_id, task_id, events),
            encoding="utf-8",
        )
        files[2].write_text(
            _gate_markdown(
                "PRD Gate",
                by_gate.get("prd_review", ()),
            ),
            encoding="utf-8",
        )
        files[3].write_text(
            _gate_markdown(
                "TDD Gate",
                by_gate.get("tdd_review", ()),
            ),
            encoding="utf-8",
        )
        files[4].write_text(
            _grill_markdown(events),
            encoding="utf-8",
        )
        files[5].write_text(
            _issues_markdown(events),
            encoding="utf-8",
        )
        files[6].write_text(
            _screenshots_markdown(screenshot_files),
            encoding="utf-8",
        )
        outcome_review_events = by_gate.get("outcome_review", ())
        outcome_review_markdown = _gate_markdown(
            "Outcome Review Gate",
            outcome_review_events,
        )
        files[7].write_text(
            (
                files[7].read_text(encoding="utf-8")
                if files[7].exists()
                else outcome_review_markdown
            ),
            encoding="utf-8",
        )
        files[8].write_text(
            _interactions_markdown(run_id, task_id, events),
            encoding="utf-8",
        )
        files[9].write_text(
            _transcript_markdown(run_id, task_id, events),
            encoding="utf-8",
        )
        files[10].write_text(
            transcript_jsonl,
            encoding="utf-8",
        )
        files[11].write_text(
            _mast_coverage_markdown(mast_coverage),
            encoding="utf-8",
        )
        replay_manifest = _replay_manifest(
            run_id=run_id,
            task_id=task_id,
            events=events,
            transcript_jsonl=transcript_jsonl,
            workspace_snapshot=workspace_snapshot,
            mast_coverage=mast_coverage,
            provider_model_resolutions=provider_model_resolutions,
            canonical_tool_contracts=canonical_tool_contracts,
            runtime_component_receipts=runtime_component_receipts,
            production_trace_manifest=production_trace_manifest,
            ledger_manifest=ledger_manifest,
            preserved_artifacts=preserved_artifact_manifest,
            unresolved_artifacts=unresolved_artifact_manifest,
            trusted_workspace_root=trusted_workspace_root,
        )
        replay_manifest_text = json.dumps(
            replay_manifest,
            indent=2,
            sort_keys=True,
        ) + "\n"
        files[12].write_text(
            replay_manifest_text,
            encoding="utf-8",
        )
        files[13].write_text(
            json.dumps(
                workspace_snapshot,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        files[14].write_text(
            json.dumps(
                mast_coverage,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        export_integrity_path = (
            staging_dir / "replay" / "export-integrity.json"
        )
        generated_files = _unique_paths((
            *files,
            *tuple(path for path, _ in screenshot_files),
            *production_trace_files,
            *preserved_artifact_files,
        ))
        export_integrity = _write_export_integrity(
            staging_dir,
            path=export_integrity_path,
            ledger_manifest=ledger_manifest,
            generated_files=generated_files,
        )
        all_files = (*generated_files, export_integrity_path)
        _fsync_export_tree(staging_dir, files=all_files)
        relative_files = tuple(
            path.relative_to(staging_dir)
            for path in all_files
        )
        _publish_staged_export(
            staging_dir,
            destination=out_dir,
        )
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise

    manifest_sha256 = sha256(replay_manifest_text.encode("utf-8")).hexdigest()
    state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind="dual_agent_replay_manifest_recorded",
        payload={
            "schema_version": "dual-agent-replay-manifest-record/v1",
            "run_id": run_id,
            "task_id": task_id,
            "manifest_path": str(
                (out_dir / relative_files[12]).expanduser().resolve()
            ),
            "manifest_sha256": manifest_sha256,
            "export_root_sha256": export_integrity[
                "export_root_sha256"
            ],
            "file_tree_sha256": export_integrity["file_tree_sha256"],
            "ledger_head_event_hash": ledger_manifest[
                "head_event_hash"
            ],
            "execution_provenance_status": replay_manifest[
                "execution_provenance"
            ]["status"],
            "workspace_capture_source": workspace_snapshot.get("capture_source"),
        },
    )
    provenance_issues = execution_provenance_issues(
        replay_manifest.get("execution_provenance")
    )
    trace_incomplete = (
        require_complete_trace
        and _has_trace_requiring_gate_result(events)
        and production_trace_manifest["status"] != "complete"
    )
    ledger_incomplete = ledger_manifest["status"] != "verified_structural_prefix"
    authoritative_ledger_incomplete = (
        require_authoritative_ledger
        and not ledger_manifest["authoritative_head_verified"]
    )
    return DualAgentArtifactExport(
        status=(
            "incomplete"
            if (
                (
                    require_complete_provenance
                    and _has_accepted_gate_result(events)
                    and provenance_issues
                )
                or trace_incomplete
                or ledger_incomplete
                or authoritative_ledger_incomplete
            )
            else "ok"
        ),
        output_dir=out_dir,
        files=tuple(
            out_dir / relative_path
            for relative_path in relative_files
        ),
        export_root_sha256=export_integrity["export_root_sha256"],
        ledger_head_hash=ledger_manifest["head_event_hash"],
        ledger_authoritative=bool(
            ledger_manifest["authoritative_head_verified"]
        ),
    )


def _canonical_sha256_or_none(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else None


def _canonical_export_relative_path(value: Any) -> str | None:
    text = str(value or "")
    if not text or "\\" in text:
        return None
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or text != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return None
    return text


def _read_export_regular_file(root: Path, relative: str) -> bytes:
    canonical = _canonical_export_relative_path(relative)
    if canonical is None:
        raise ValueError("path is not a canonical package-relative path")
    return _read_regular_file_beneath(root, canonical)


def _read_regular_file_beneath(root: Path, relative: str) -> bytes:
    canonical = _canonical_export_relative_path(relative)
    if canonical is None:
        raise ValueError("path is not a canonical root-relative path")
    directory_fds = _open_absolute_directory_chain_no_follow(root)
    file_fd: int | None = None
    try:
        parent_fd = directory_fds[-1]
        parts = PurePosixPath(canonical).parts
        for part in parts[:-1]:
            parent_fd = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            directory_fds.append(parent_fd)
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("package member is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _open_absolute_directory_chain_no_follow(path: Path) -> list[int]:
    absolute = path.expanduser().absolute()
    if not absolute.is_absolute() or not absolute.anchor:
        raise ValueError("trusted directory root must be absolute")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        descriptor = os.open(absolute.anchor, flags)
        descriptors.append(descriptor)
        for part in absolute.parts[1:]:
            descriptor = os.open(
                part,
                flags,
                dir_fd=descriptor,
            )
            descriptors.append(descriptor)
        return descriptors
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _export_file_descriptors(
    integrity: Mapping[str, Any],
    *,
    issues: list[str],
) -> list[dict[str, Any]]:
    raw_files = integrity.get("files")
    if not isinstance(raw_files, list):
        issues.append("export file descriptors are not a list")
        return []
    descriptors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_files):
        if not isinstance(raw, Mapping):
            issues.append(f"export file descriptor {index} is not an object")
            continue
        relative = _canonical_export_relative_path(raw.get("path"))
        size = raw.get("size")
        digest = _canonical_sha256_or_none(raw.get("sha256"))
        if relative is None:
            issues.append(
                f"export file descriptor {index} has an invalid path"
            )
            continue
        if relative in seen:
            issues.append(f"duplicate export file descriptor: {relative}")
            continue
        if type(size) is not int or size < 0:
            issues.append(
                f"export file descriptor {relative!r} has an invalid size"
            )
            continue
        if digest is None:
            issues.append(
                f"export file descriptor {relative!r} has an invalid sha256"
            )
            continue
        seen.add(relative)
        descriptors.append({
            "path": relative,
            "size": size,
            "sha256": digest,
        })
    if [item["path"] for item in descriptors] != sorted(seen):
        issues.append("export file descriptors are not canonically ordered")
    return descriptors


def _strict_canonical_jsonl(
    content: bytes,
    *,
    label: str,
    issues: list[str],
) -> list[dict[str, Any]]:
    if not content or not content.endswith(b"\n"):
        issues.append(f"{label} is empty or lacks its final newline")
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in content.splitlines():
            if not line:
                raise ValueError("blank JSONL row")
            rows.append(strict_json_object_loads(line))
    except (TypeError, UnicodeDecodeError, ValueError) as exc:
        issues.append(f"{label} is not strict JSONL: {exc}")
        return []
    canonical = b"".join(
        canonical_json_bytes(row) + b"\n"
        for row in rows
    )
    if canonical != content:
        issues.append(f"{label} is not canonically encoded")
    return rows


def _strict_jsonl_objects(
    content: bytes,
    *,
    label: str,
    issues: list[str],
) -> list[dict[str, Any]]:
    if not content or not content.endswith(b"\n"):
        issues.append(f"{label} is empty or lacks its final newline")
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in content.splitlines():
            if not line:
                raise ValueError("blank JSONL row")
            rows.append(strict_json_object_loads(line))
    except (TypeError, UnicodeDecodeError, ValueError) as exc:
        issues.append(f"{label} is not strict JSONL: {exc}")
        return []
    return rows


def _compare_export_ledger_manifest(
    manifest: Mapping[str, Any],
    *,
    verification: Any,
    issues: list[str],
) -> None:
    expected_exact = {
        "event_count": verification.event_count,
        "head_event_id": verification.head_event_id,
        "captured_head_event_id": verification.head_event_id,
        "head_event_hash": verification.head_event_hash,
        "head_event_identity_hash": (
            verification.head_event_identity_hash
        ),
    }
    for field, expected in expected_exact.items():
        if manifest.get(field) != expected:
            issues.append(
                f"export ledger manifest {field} differs from verified ledger"
            )
    if manifest.get("status") != "verified_structural_prefix":
        issues.append("export ledger manifest status is not verified")
    if manifest.get("scope") != "run_genesis_through_captured_head":
        issues.append("export ledger manifest scope is not canonical")
    if type(manifest.get("authoritative_head_verified")) is not bool:
        issues.append(
            "export ledger authoritative-head status is not boolean"
        )
    elif manifest.get("authoritative_head_verified"):
        if manifest.get("authority_failure_code") is not None:
            issues.append(
                "authoritative export ledger carries a failure code"
            )
        if not str(manifest.get("external_anchor_ref") or "").strip():
            issues.append(
                "authoritative export ledger lacks its external anchor ref"
            )


def _verify_export_replay_manifest(
    package: Path,
    *,
    file_bytes: Mapping[str, bytes],
    integrity_ledger: Mapping[str, Any],
    ledger_rows: list[dict[str, Any]],
    issues: list[str],
) -> None:
    manifest_path = "replay/manifest.json"
    manifest_bytes = file_bytes.get(manifest_path)
    if manifest_bytes is None:
        issues.append("replay manifest is absent from the export")
        return
    try:
        manifest = strict_json_object_loads(manifest_bytes)
    except (TypeError, UnicodeDecodeError, ValueError) as exc:
        issues.append(f"replay manifest is invalid: {exc}")
        return
    if manifest.get("schema_version") != "dual-agent-replay-manifest/v1":
        issues.append("unsupported replay manifest schema")
    if manifest.get("ledger") != dict(integrity_ledger):
        issues.append(
            "replay manifest ledger differs from export integrity ledger"
        )
    run_id = str(integrity_ledger.get("run_id") or "")
    if manifest.get("run_id") != run_id:
        issues.append("replay manifest run_id differs from the ledger")
    task_id = str(manifest.get("task_id") or "").strip()
    if not task_id:
        issues.append("replay manifest task_id is empty")
        task_events: list[dict[str, Any]] = []
    else:
        task_events = _task_events_from_ledger_rows(
            ledger_rows,
            task_id=task_id,
        )
    expected_event_ids = [
        int(event["event_id"])
        for event in task_events
    ]
    raw_event_ids = manifest.get("event_ids")
    if (
        not isinstance(raw_event_ids, list)
        or any(type(event_id) is not int for event_id in raw_event_ids)
        or raw_event_ids != expected_event_ids
    ):
        issues.append(
            "replay manifest task event projection differs from the ledger"
        )
    if manifest.get("events_count") != len(task_events):
        issues.append(
            "replay manifest task event count differs from the ledger"
        )
    state_projection = manifest.get("state")
    expected_state = {
        "first_event_id": (
            expected_event_ids[0] if expected_event_ids else 0
        ),
        "last_event_id": (
            expected_event_ids[-1] if expected_event_ids else 0
        ),
        "events_count_at_capture": len(expected_event_ids),
    }
    if not isinstance(state_projection, Mapping):
        issues.append("replay manifest state projection is not an object")
    else:
        for field, expected in expected_state.items():
            if state_projection.get(field) != expected:
                issues.append(
                    "replay manifest state projection differs from the "
                    f"ledger: {field}"
                )
    expected_event_kinds = sorted({
        str(event["kind"])
        for event in task_events
    })
    if manifest.get("event_kinds") != expected_event_kinds:
        issues.append(
            "replay manifest event kinds differ from the ledger"
        )
    transcript_bytes = file_bytes.get("transcript.jsonl")
    if transcript_bytes is None:
        issues.append("machine transcript is absent from the export")
    else:
        transcript_rows = _strict_jsonl_objects(
            transcript_bytes,
            label="machine transcript",
            issues=issues,
        )
        if transcript_rows != task_events:
            issues.append(
                "transcript event projection differs from the ledger"
            )
        if (
            isinstance(state_projection, Mapping)
            and _canonical_sha256_or_none(
                state_projection.get("transcript_jsonl_sha256")
            )
            != sha256(transcript_bytes).hexdigest()
        ):
            issues.append(
                "machine transcript hash differs from the replay manifest"
            )
    _verify_preserved_artifact_manifest(
        manifest,
        file_bytes=file_bytes,
        task_events=task_events,
        issues=issues,
    )

    raw_trace = manifest.get("production_trace")
    if not isinstance(raw_trace, Mapping):
        issues.append("replay manifest production trace is not an object")
        return
    if (
        raw_trace.get("schema_version")
        != "dual-agent-production-trace-export/v1"
    ):
        issues.append("unsupported production trace export schema")
    rows_by_id = {
        int(row["event_id"]): row
        for row in ledger_rows
    }
    expected_recorded_events = [
        event
        for event in task_events
        if event["kind"] == "dual_agent_production_trace_recorded"
    ]
    expected_recorded_ids = [
        int(event["event_id"])
        for event in expected_recorded_events
    ]
    expected_recorded_by_id = {
        int(event["event_id"]): event
        for event in expected_recorded_events
    }
    raw_records = raw_trace.get("records")
    if not isinstance(raw_records, list):
        issues.append("production trace records are not a list")
        raw_records = []
    actual_recorded_ids = [
        record.get("event_id")
        for record in raw_records
        if isinstance(record, Mapping)
    ]
    if (
        any(type(event_id) is not int for event_id in actual_recorded_ids)
        or actual_recorded_ids != expected_recorded_ids
        or len(set(actual_recorded_ids)) != len(actual_recorded_ids)
    ):
        issues.append(
            "production trace record coverage differs from the ledger"
        )

    expected_failed_events = [
        event
        for event in task_events
        if event["kind"] == "dual_agent_production_trace_failed"
    ]
    expected_failed_ids = [
        int(event["event_id"])
        for event in expected_failed_events
    ]
    raw_failed_attempts = raw_trace.get("failed_attempts")
    if raw_failed_attempts is None and not expected_failed_ids:
        raw_failed_attempts = []
    if not isinstance(raw_failed_attempts, list):
        issues.append("production trace failed attempts are not a list")
        raw_failed_attempts = []
    actual_failed_ids = [
        attempt.get("event_id")
        for attempt in raw_failed_attempts
        if isinstance(attempt, Mapping)
    ]
    if (
        any(type(event_id) is not int for event_id in actual_failed_ids)
        or actual_failed_ids != expected_failed_ids
        or len(set(actual_failed_ids)) != len(actual_failed_ids)
    ):
        issues.append(
            "production trace failure coverage differs from the ledger"
        )

    required_source_ids = [
        int(event["event_id"])
        for event in _trace_requiring_source_events(task_events)
    ]
    recorded_source_ids = [
        event["payload"].get("source_event_id")
        for event in expected_recorded_events
    ]
    for source_event_id in sorted({
        source_event_id
        for source_event_id in recorded_source_ids
        if type(source_event_id) is int
    }):
        if recorded_source_ids.count(source_event_id) != 1:
            issues.append(
                "production trace authority is not one-to-one for "
                f"source event {source_event_id}"
            )
    for source_event_id in required_source_ids:
        coverage_count = recorded_source_ids.count(source_event_id)
        if coverage_count == 0:
            issues.append(
                "required production trace authority is missing for "
                f"source event {source_event_id}"
            )
        elif coverage_count != 1:
            issues.append(
                "required production trace authority is not one-to-one "
                f"for source event {source_event_id}"
            )

    trace_status = raw_trace.get("status")
    if required_source_ids and trace_status != "complete":
        issues.append("required production trace authority is missing")
    elif (
        (expected_recorded_ids or expected_failed_ids)
        and trace_status != "complete"
    ):
        issues.append("production trace export status is not complete")
    elif (
        not expected_recorded_ids
        and not expected_failed_ids
        and not required_source_ids
        and trace_status != "missing"
    ):
        issues.append("production trace export status is not missing")
    raw_trace_issues = raw_trace.get("issues")
    if (
        not isinstance(raw_trace_issues, list)
        or any(not isinstance(issue, str) for issue in raw_trace_issues)
    ):
        issues.append("production trace issues are not a string list")
    elif trace_status == "complete" and raw_trace_issues:
        issues.append("complete production trace export contains issues")

    seen_receipt_paths: set[str] = set()
    verified_record_event_ids: set[int] = set()
    for raw_record in raw_records:
        record_issue_start = len(issues)
        if not isinstance(raw_record, Mapping):
            issues.append("production trace record is not an object")
            continue
        if raw_record.get("status") != "complete":
            issues.append("production trace record status is not complete")
            continue
        event_id = raw_record.get("event_id")
        expected_event = (
            expected_recorded_by_id.get(event_id)
            if type(event_id) is int
            else None
        )
        if expected_event is None:
            issues.append(
                "production trace record does not identify a canonical "
                "ledger event"
            )
            continue
        if raw_record.get("binding_status") != "verified":
            issues.append("production trace record binding is not verified")
        if raw_record.get("authority_status") != "verified":
            issues.append("production trace record authority is not verified")
        if raw_record.get("issues") != []:
            issues.append("complete production trace record contains issues")
        if raw_record.get("recorded_event") != _ledger_event_ref(
            rows_by_id[event_id]
        ):
            issues.append(
                "production trace recorded-event reference differs from "
                "the ledger"
            )
        expected_source_event_id = expected_event["payload"].get(
            "source_event_id"
        )
        expected_source_event_hash = expected_event["payload"].get(
            "source_event_hash"
        )
        if (
            raw_record.get("source_event_id") != expected_source_event_id
            or raw_record.get("source_event_hash")
            != expected_source_event_hash
        ):
            issues.append(
                "production trace source binding differs from the "
                "recorded ledger event"
            )
        receipt_path = _canonical_export_relative_path(
            raw_record.get("receipt_path")
        )
        if receipt_path is None:
            issues.append("production trace receipt path is invalid")
            continue
        if receipt_path in seen_receipt_paths:
            issues.append(
                f"duplicate production trace receipt path: {receipt_path}"
            )
            continue
        seen_receipt_paths.add(receipt_path)
        receipt_bytes = file_bytes.get(receipt_path)
        if receipt_bytes is None:
            issues.append(
                f"production trace receipt is absent: {receipt_path}"
            )
            continue
        if (
            _canonical_sha256_or_none(raw_record.get("receipt_sha256"))
            != sha256(receipt_bytes).hexdigest()
        ):
            issues.append(
                f"production trace receipt hash is invalid: {receipt_path}"
            )
            continue
        try:
            receipt = strict_json_object_loads(receipt_bytes)
        except (TypeError, UnicodeDecodeError, ValueError) as exc:
            issues.append(
                f"production trace receipt is invalid: {receipt_path}: {exc}"
            )
            continue
        binding_issues = _production_trace_ledger_binding(
            expected_event,
            receipt=receipt,
            ledger_by_event_id=rows_by_id,
        )[2]
        issues.extend(
            "production trace ledger binding failed: " + issue
            for issue in binding_issues
        )
        source_event_id = expected_source_event_id
        source_row = (
            rows_by_id.get(source_event_id)
            if type(source_event_id) is int
            else None
        )
        if source_row is None:
            issues.append(
                "production trace source event is absent from the ledger"
            )
            continue
        if raw_record.get("source_event") != _ledger_event_ref(source_row):
            issues.append(
                "production trace source-event reference differs from "
                "the ledger"
            )
        public_artifacts = raw_record.get("public_artifacts")
        if not isinstance(public_artifacts, Mapping):
            issues.append(
                "production trace public artifacts are not an object"
            )
            continue
        if receipt.get("public_artifacts") != public_artifacts:
            issues.append(
                "production trace receipt public artifacts differ from "
                "the manifest"
            )
        artifact_paths: dict[str, Path] = {}
        for label in ("trace_store", "gradebook"):
            descriptor = public_artifacts.get(label)
            if not isinstance(descriptor, Mapping):
                issues.append(
                    f"production trace {label} descriptor is absent"
                )
                continue
            relative = _canonical_export_relative_path(
                descriptor.get("path")
            )
            if relative is None or relative not in file_bytes:
                issues.append(
                    f"production trace {label} file is absent"
                )
                continue
            if (
                _canonical_sha256_or_none(descriptor.get("sha256"))
                != sha256(file_bytes[relative]).hexdigest()
            ):
                issues.append(
                    f"production trace {label} hash is invalid"
                )
                continue
            artifact_paths[label] = package / relative
        if set(artifact_paths) != {"trace_store", "gradebook"}:
            continue
        authority_issues = _production_trace_authority_issues(
            trace_store_path=artifact_paths["trace_store"],
            gradebook_path=artifact_paths["gradebook"],
            receipt=receipt,
            source_row=source_row,
        )
        issues.extend(authority_issues)
        if len(issues) == record_issue_start:
            verified_record_event_ids.add(event_id)

    raw_failed_by_id = {
        attempt["event_id"]: attempt
        for attempt in raw_failed_attempts
        if isinstance(attempt, Mapping)
        and type(attempt.get("event_id")) is int
    }
    for failed_event in expected_failed_events:
        failed_event_id = int(failed_event["event_id"])
        failed_identity = _production_trace_attempt_identity(failed_event)
        recovered_by = next(
            (
                recorded_event_id
                for recorded_event_id in expected_recorded_ids
                if recorded_event_id in verified_record_event_ids
                and recorded_event_id > failed_event_id
                and _production_trace_attempt_identity(
                    expected_recorded_by_id[recorded_event_id]
                ) == failed_identity
            ),
            None,
        )
        expected_attempt = {
            "event_id": failed_event_id,
            "source_event_id": failed_event["payload"].get(
                "source_event_id"
            ),
            "source_event_hash": failed_event["payload"].get(
                "source_event_hash"
            ),
            "status": (
                "recovered"
                if recovered_by is not None
                else "blocking"
            ),
            "recovered_by_event_id": recovered_by,
        }
        if raw_failed_by_id.get(failed_event_id) != expected_attempt:
            issues.append(
                "production trace failed-attempt recovery differs from "
                f"the ledger for event {failed_event_id}"
            )
        if recovered_by is None:
            issues.append(
                "production trace recording failure remains blocking for "
                f"event {failed_event_id}"
            )


def _verify_preserved_artifact_manifest(
    manifest: Mapping[str, Any],
    *,
    file_bytes: Mapping[str, bytes],
    task_events: list[dict[str, Any]],
    issues: list[str],
) -> None:
    raw_descriptors = manifest.get("preserved_artifacts")
    if not isinstance(raw_descriptors, list):
        issues.append("preserved artifact manifest is not a list")
        return
    trusted_refs = {
        (
            ref.source_event_id,
            ref.source_kind,
            ref.source_path,
            ref.sha256,
        )
        for ref in _bound_export_artifact_refs(task_events)
    }
    described_paths: list[str] = []
    preserved_digests: set[str] = set()
    for index, raw in enumerate(raw_descriptors):
        if not isinstance(raw, Mapping):
            issues.append(
                f"preserved artifact descriptor {index} is not an object"
            )
            continue
        relative = _canonical_export_relative_path(raw.get("path"))
        digest = _canonical_sha256_or_none(raw.get("sha256"))
        source_event_id = raw.get("source_event_id")
        source_kind = str(raw.get("source_kind") or "")
        source_path = str(raw.get("source_path") or "")
        if (
            relative is None
            or digest is None
            or type(source_event_id) is not int
            or not source_kind
            or not source_path
        ):
            issues.append(
                f"preserved artifact descriptor {index} is invalid"
            )
            continue
        described_paths.append(relative)
        content = file_bytes.get(relative)
        if content is None or sha256(content).hexdigest() != digest:
            issues.append(
                f"preserved artifact bytes differ from the descriptor: "
                f"{relative}"
            )
        else:
            preserved_digests.add(digest)
        if (
            source_event_id,
            source_kind,
            source_path,
            digest,
        ) not in trusted_refs:
            issues.append(
                "preserved artifact ledger binding is invalid: "
                f"{relative}"
            )
    if described_paths != sorted(set(described_paths)):
        issues.append(
            "preserved artifact descriptors are not unique and ordered"
        )
    raw_unresolved = manifest.get("unresolved_artifacts")
    if raw_unresolved is None:
        raw_unresolved = []
    if not isinstance(raw_unresolved, list):
        issues.append("unresolved artifact manifest is not a list")
        raw_unresolved = []
    unresolved_keys: set[tuple[int, str, str, str]] = set()
    for index, raw in enumerate(raw_unresolved):
        if not isinstance(raw, Mapping):
            issues.append(
                f"unresolved artifact descriptor {index} is not an object"
            )
            continue
        digest = _canonical_sha256_or_none(raw.get("sha256"))
        source_event_id = raw.get("source_event_id")
        source_kind = str(raw.get("source_kind") or "")
        source_path = str(raw.get("source_path") or "")
        if (
            digest is None
            or type(source_event_id) is not int
            or not source_kind
            or not source_path
        ):
            issues.append(
                f"unresolved artifact descriptor {index} is invalid"
            )
            continue
        key = (source_event_id, source_kind, source_path, digest)
        if key not in trusted_refs:
            issues.append(
                "unresolved artifact ledger binding is invalid: "
                f"{source_path}"
            )
            continue
        unresolved_keys.add(key)
    for ref_key in sorted(trusted_refs):
        _event_id, _kind, ref_path, ref_digest = ref_key
        if ref_digest in preserved_digests or ref_key in unresolved_keys:
            continue
        issues.append(
            "ledger-hash-bound artifact is not preserved or recorded "
            f"as unresolved: {ref_path}"
        )
    allowed_exact = {
        *_NON_PRESERVABLE_EXPORT_PATHS,
        "outcome-review.md",
    }
    unexpected = sorted(
        path
        for path in file_bytes
        if (
            path not in allowed_exact
            and path not in set(described_paths)
            and not path.startswith("screenshots/")
            and not path.startswith("replay/production-traces/")
        )
    )
    if unexpected:
        issues.append(
            "export contains files without a generated or ledger-bound "
            f"purpose: {unexpected}"
        )


def _prepare_export_destination(path: Path) -> None:
    absolute = path.expanduser().absolute()
    _reject_symlink_components(absolute)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(absolute)
    if os.path.lexists(absolute):
        if absolute.is_symlink():
            raise ValueError(
                f"export destination is a symlink: {absolute}"
            )
        if not absolute.is_dir():
            raise ValueError(
                f"export destination is not a directory: {absolute}"
            )
        _reject_tree_symlinks(absolute)


def _reject_symlink_components(path: Path) -> None:
    candidate = Path(path.anchor)
    for part in path.parts[1:]:
        candidate /= part
        if os.path.lexists(candidate) and candidate.is_symlink():
            raise ValueError(
                f"export path contains a symlink: {candidate}"
            )


def _reject_tree_symlinks(root: Path) -> None:
    for current_root, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
        current = Path(current_root)
        for name in (*directory_names, *file_names):
            candidate = current / name
            if candidate.is_symlink():
                raise ValueError(
                    f"export tree contains a symlink: {candidate}"
                )


def _copy_hash_bound_export_artifacts(
    events: list[dict[str, Any]],
    existing_output_dir: Path,
    staging_dir: Path,
    *,
    trusted_workspace_root: str | Path | None,
) -> tuple[
    tuple[Path, ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
]:
    output_root = existing_output_dir.expanduser().absolute()
    workspace_roots = _artifact_workspace_roots(
        events,
        trusted_workspace_root=trusted_workspace_root,
    )
    task_root = (
        output_root.parent
        if output_root.name == "release"
        else output_root
    )
    trusted_read_roots = _unique_paths((
        *workspace_roots,
        output_root,
        task_root,
    ))
    copied_by_relative: dict[str, Path] = {}
    descriptors_by_relative: dict[str, dict[str, Any]] = {}
    unresolved_by_key: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    expected_hashes: dict[str, str] = {}
    for ref in _bound_export_artifact_refs(events):
        candidates = _bound_export_artifact_candidates(
            ref.source_path,
            output_root=output_root,
            workspace_roots=workspace_roots,
        )
        if not candidates:
            unresolved_by_key[(
                ref.source_event_id,
                ref.source_kind,
                ref.source_path,
                ref.sha256,
            )] = {
                "sha256": ref.sha256,
                "source_event_id": ref.source_event_id,
                "source_kind": ref.source_kind,
                "source_path": ref.source_path,
                "reason": "no_path_candidates",
            }
            continue
        matching: list[tuple[Path, str, bytes]] = []
        for source, relative in candidates:
            if not _preservable_export_relative_path(relative):
                continue
            try:
                content = _read_regular_path_no_follow(
                    source,
                    trusted_roots=trusted_read_roots,
                )
            except (OSError, ValueError):
                continue
            if sha256(content).hexdigest() == ref.sha256:
                matching.append((source, relative, content))
        if not matching:
            candidate_text = ", ".join(
                f"{source} -> {relative}"
                for source, relative in candidates
            ) or "<none>"
            raise ValueError(
                "hash-bound export artifact cannot be resolved at its "
                f"ledger-pinned hash: {ref.source_path}: {candidate_text}"
            )
        relative_paths = {relative for _, relative, _ in matching}
        if len(relative_paths) != 1:
            raise ValueError(
                "hash-bound export artifact resolves to conflicting package "
                f"paths: {ref.source_path}: {sorted(relative_paths)}"
            )
        _source, relative, content = matching[0]
        prior_hash = expected_hashes.get(relative)
        if prior_hash is not None and prior_hash != ref.sha256:
            raise ValueError(
                "conflicting hash-bound export artifact references: "
                f"{relative}"
            )
        expected_hashes[relative] = ref.sha256
        target = staging_dir / Path(PurePosixPath(relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative not in copied_by_relative:
            target.write_bytes(content)
            copied_by_relative[relative] = target
        descriptors_by_relative[relative] = {
            "path": relative,
            "sha256": ref.sha256,
            "source_event_id": ref.source_event_id,
            "source_kind": ref.source_kind,
            "source_path": ref.source_path,
        }
    ordered = sorted(copied_by_relative)
    return (
        tuple(copied_by_relative[relative] for relative in ordered),
        tuple(descriptors_by_relative[relative] for relative in ordered),
        tuple(
            unresolved_by_key[key]
            for key in sorted(unresolved_by_key)
        ),
    )


def _bound_export_artifact_refs(
    events: Iterable[Mapping[str, Any]],
) -> tuple[_BoundArtifactRef, ...]:
    refs: list[_BoundArtifactRef] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        event_id = int(event["event_id"])
        kind = str(event.get("kind") or "")
        if kind == "supervisor_review_packet_created":
            refs.extend(
                _review_packet_artifact_refs(
                    event_id=event_id,
                    event_kind=kind,
                    payload=payload,
                )
            )
        elif (
            kind == "dual_agent_dynamic_workflow_manifest"
            and str(payload.get("status") or "").lower() == "accepted"
        ):
            refs.extend(
                _mapping_hash_bound_refs(
                    payload,
                    event_id=event_id,
                    event_kind=kind,
                )
            )
        elif (
            kind == "dual_agent_dynamic_workflow_receipt_validation"
            and str(payload.get("status") or "").lower() == "accepted"
            and isinstance(payload.get("probe"), Mapping)
            and str(payload["probe"].get("status") or "").lower() == "green"
        ):
            receipts = payload.get("tool_receipts")
            if isinstance(receipts, list):
                for receipt in receipts:
                    if isinstance(receipt, Mapping):
                        refs.extend(
                            _mapping_hash_bound_refs(
                                receipt,
                                event_id=event_id,
                                event_kind=kind,
                            )
                        )
        elif (
            kind == "dual_agent_runtime_evidence"
            and isinstance(payload.get("probe"), Mapping)
            and str(payload["probe"].get("status") or "").lower() == "green"
        ):
            refs.extend(
                _mapping_hash_bound_refs(
                    payload,
                    event_id=event_id,
                    event_kind=kind,
                )
            )
        elif kind == "dual_agent_gate_result":
            refs.extend(
                _accepted_handoff_artifact_refs(
                    event_id=event_id,
                    event_kind=kind,
                    payload=dict(payload),
                )
            )
    unique: dict[tuple[int, str, str, str], _BoundArtifactRef] = {}
    for ref in refs:
        unique[(
            ref.source_event_id,
            ref.source_kind,
            ref.source_path,
            ref.sha256,
        )] = ref
    return tuple(unique[key] for key in sorted(unique))


def _review_packet_artifact_refs(
    *,
    event_id: int,
    event_kind: str,
    payload: Mapping[str, Any],
) -> tuple[_BoundArtifactRef, ...]:
    validation = payload.get("validation")
    if (
        payload.get("schema_version") != "supervisor-review-packet/v1"
        or not isinstance(validation, Mapping)
        or validation.get("status") != "passed"
    ):
        return ()
    try:
        packet = build_review_packet(
            task_id=str(payload.get("task_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            gate=str(payload.get("gate") or ""),
            packet_id=str(payload.get("packet_id") or ""),
            base_head=str(payload.get("base_head") or ""),
            candidate_head=(
                None
                if payload.get("candidate_head") is None
                else str(payload.get("candidate_head"))
            ),
            patch_hash=(
                None
                if payload.get("patch_hash") is None
                else str(payload.get("patch_hash"))
            ),
            planning_refs=(
                payload.get("planning_refs")
                if isinstance(payload.get("planning_refs"), list)
                else ()
            ),
            acceptance_items=payload.get("acceptance_items") or (),
            diff_refs=payload.get("diff_refs") or (),
            name_status_refs=payload.get("name_status_refs") or (),
            changed_files=(
                payload.get("changed_files")
                if isinstance(payload.get("changed_files"), list)
                else ()
            ),
            runtime_receipt_ids=payload.get("runtime_receipt_ids") or (),
            declared_tests=payload.get("declared_tests") or (),
            executed_test_receipt_ids=(
                payload.get("executed_test_receipt_ids") or ()
            ),
            dependency_refs=payload.get("dependency_refs") or (),
            policy_overlay_hash=str(
                payload.get("policy_overlay_hash") or ""
            ),
            lesson_hashes=payload.get("lesson_hashes") or (),
            reviewer_ids=payload.get("reviewer_ids") or (),
            implementer_transcript_ref=(
                None
                if payload.get("implementer_transcript_ref") is None
                else str(payload.get("implementer_transcript_ref"))
            ),
        )
    except (AttributeError, TypeError, ValueError):
        return ()
    if packet.packet_sha256 != _canonical_sha256_or_none(
        payload.get("packet_sha256")
    ):
        return ()
    refs: list[_BoundArtifactRef] = []
    for changed_file in packet.changed_files:
        digest = _canonical_sha256_or_none(changed_file.sha256)
        if digest is None or str(changed_file.status).upper() == "D":
            continue
        refs.append(_BoundArtifactRef(
            source_event_id=event_id,
            source_kind=event_kind,
            source_path=changed_file.path,
            sha256=digest,
        ))
    for planning_ref in packet.planning_refs:
        digest = _canonical_sha256_or_none(planning_ref.sha256)
        if digest is None:
            continue
        refs.append(_BoundArtifactRef(
            source_event_id=event_id,
            source_kind=event_kind,
            source_path=planning_ref.path,
            sha256=digest,
        ))
    return tuple(refs)


def _accepted_handoff_artifact_refs(
    *,
    event_id: int,
    event_kind: str,
    payload: dict[str, Any],
) -> tuple[_BoundArtifactRef, ...]:
    acceptance = _load_acceptance_snapshot(payload)
    packet = (
        acceptance.get("handoff_packet")
        if isinstance(acceptance, Mapping)
        else None
    )
    if (
        not isinstance(packet, Mapping)
        or packet.get("status") != "captured"
    ):
        return ()
    content = packet.get("content")
    if not isinstance(content, str):
        return ()
    expected_packet_hash = _canonical_sha256_or_none(packet.get("sha256"))
    if (
        expected_packet_hash is None
        or sha256(content.encode("utf-8")).hexdigest()
        != expected_packet_hash
    ):
        return ()
    try:
        handoff = strict_json_object_loads(content)
    except (TypeError, UnicodeDecodeError, ValueError):
        return ()
    artifacts = handoff.get("planning_artifacts")
    if not isinstance(artifacts, list):
        return ()
    refs: list[_BoundArtifactRef] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        source_path = str(artifact.get("path") or "").strip()
        digest = _canonical_sha256_or_none(artifact.get("sha256"))
        if source_path and digest is not None:
            refs.append(_BoundArtifactRef(
                source_event_id=event_id,
                source_kind=event_kind,
                source_path=source_path,
                sha256=digest,
            ))
    return tuple(refs)


def _mapping_hash_bound_refs(
    value: Any,
    *,
    event_id: int,
    event_kind: str,
) -> tuple[_BoundArtifactRef, ...]:
    refs: list[_BoundArtifactRef] = []
    if isinstance(value, Mapping):
        for path_key, path_value in value.items():
            if not isinstance(path_key, str):
                continue
            if path_key == "path":
                hash_key = "sha256"
            elif path_key.endswith("_path"):
                hash_key = f"{path_key[:-5]}_sha256"
            elif path_key.endswith("_ref"):
                hash_key = f"{path_key[:-4]}_sha256"
            else:
                continue
            source_path = str(path_value or "").strip()
            digest = _canonical_sha256_or_none(value.get(hash_key))
            if source_path and digest is not None:
                refs.append(_BoundArtifactRef(
                    source_event_id=event_id,
                    source_kind=event_kind,
                    source_path=source_path,
                    sha256=digest,
                ))
        for nested in value.values():
            refs.extend(
                _mapping_hash_bound_refs(
                    nested,
                    event_id=event_id,
                    event_kind=event_kind,
                )
            )
    elif isinstance(value, (list, tuple)):
        for nested in value:
            refs.extend(
                _mapping_hash_bound_refs(
                    nested,
                    event_id=event_id,
                    event_kind=event_kind,
                )
            )
    return tuple(refs)


def _artifact_workspace_roots(
    events: Iterable[Mapping[str, Any]],
    *,
    trusted_workspace_root: str | Path | None,
) -> tuple[Path, ...]:
    if trusted_workspace_root is not None:
        root = Path(trusted_workspace_root).expanduser().absolute()
        return (root,)
    roots: dict[str, Path] = {}
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        acceptance = _load_acceptance_snapshot(dict(payload))
        if not isinstance(acceptance, Mapping):
            continue
        snapshot = acceptance.get("workspace_snapshot")
        if isinstance(snapshot, Mapping):
            root_text = str(snapshot.get("root") or "").strip()
            if root_text:
                root = Path(root_text).expanduser().absolute()
                roots[str(root)] = root
        packet = acceptance.get("handoff_packet")
        if not isinstance(packet, Mapping):
            continue
        content = packet.get("content")
        if not isinstance(content, str):
            continue
        try:
            handoff = strict_json_object_loads(content)
        except (TypeError, UnicodeDecodeError, ValueError):
            continue
        root_text = str(handoff.get("cwd") or "").strip()
        if root_text:
            root = Path(root_text).expanduser().absolute()
            roots[str(root)] = root
    return tuple(roots[key] for key in sorted(roots))


def _bound_export_artifact_candidates(
    source_path: str,
    *,
    output_root: Path,
    workspace_roots: tuple[Path, ...],
) -> tuple[tuple[Path, str], ...]:
    raw = Path(source_path).expanduser()
    package_root = output_root.expanduser().absolute()
    task_root = (
        package_root.parent
        if package_root.name == "release"
        else package_root
    )
    candidates: dict[tuple[str, str], tuple[Path, str]] = {}

    def add(candidate: Path, relative_text: str) -> None:
        canonical = _canonical_export_relative_path(relative_text)
        if canonical is None:
            return
        absolute = candidate.expanduser().absolute()
        candidates[(str(absolute), canonical)] = (absolute, canonical)

    if raw.is_absolute():
        absolute = raw.absolute()
        try:
            add(absolute, absolute.relative_to(package_root).as_posix())
        except ValueError:
            pass
        try:
            task_relative = absolute.relative_to(task_root).as_posix()
            add(
                absolute,
                _release_relative_for_task_artifact(task_relative),
            )
        except ValueError:
            pass
        for workspace_root in workspace_roots:
            try:
                workspace_relative = absolute.relative_to(workspace_root)
            except ValueError:
                continue
            if (
                workspace_relative.parts
                and workspace_relative.parts[0]
                in _OUTPUT_RELATIVE_ARTIFACT_ROOTS
            ):
                add(absolute, workspace_relative.as_posix())
    else:
        raw_posix = raw.as_posix()
        for workspace_root in workspace_roots:
            try:
                task_relative = task_root.relative_to(workspace_root)
            except ValueError:
                task_relative = None
            if (
                task_relative is not None
                and len(raw.parts) > len(task_relative.parts)
                and raw.parts[:len(task_relative.parts)]
                == task_relative.parts
            ):
                add(
                    workspace_root / raw,
                    _release_relative_for_task_artifact(PurePosixPath(
                        *raw.parts[len(task_relative.parts):]
                    ).as_posix()),
                )
            if (
                raw.parts
                and raw.parts[0] in _OUTPUT_RELATIVE_ARTIFACT_ROOTS
            ):
                add(workspace_root / raw, raw_posix)
            if (
                len(raw.parts) >= 5
                and raw.parts[:2] == (".handoff", "workflow-jobs")
                and raw.parts[2] == task_root.name
            ):
                worker_id = raw.parts[3]
                artifact_name = PurePosixPath(
                    *raw.parts[4:]
                ).as_posix()
                add(
                    workspace_root / raw,
                    f"artifacts/dynamic/{worker_id}/{artifact_name}",
                )
        if (
            raw.parts
            and (
                raw.parts[0] in _OUTPUT_RELATIVE_ARTIFACT_ROOTS
                or raw_posix == "outcome-review.md"
            )
        ):
            add(package_root / raw, raw_posix)
            add(task_root / raw, raw_posix)
    return tuple(candidates.values())


def _release_relative_for_task_artifact(relative: str) -> str:
    if relative in _LEGACY_SOURCE_ARTIFACT_FILENAMES:
        return f"source/{relative}"
    return relative


def _preservable_export_relative_path(relative: str) -> bool:
    return (
        relative not in _NON_PRESERVABLE_EXPORT_PATHS
        and not relative.startswith("screenshots/")
        and not relative.startswith("replay/production-traces/")
    )


def _read_regular_path_no_follow(
    path: Path,
    *,
    trusted_roots: Iterable[Path],
) -> bytes:
    absolute = path.expanduser().absolute()
    candidates: list[tuple[int, Path, str]] = []
    for root in trusted_roots:
        root_absolute = root.expanduser().absolute()
        try:
            relative = absolute.relative_to(root_absolute)
        except ValueError:
            continue
        canonical = _canonical_export_relative_path(relative.as_posix())
        if canonical is None:
            continue
        candidates.append((len(root_absolute.parts), root_absolute, canonical))
    if not candidates:
        raise ValueError("path is outside the trusted artifact roots")
    _, root, relative = max(candidates, key=lambda item: item[0])
    return _read_regular_file_beneath(root, relative)


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    unique: dict[str, Path] = {}
    for path in paths:
        key = str(path.expanduser().absolute())
        unique.setdefault(key, path)
    return tuple(unique.values())


def _export_tree_files(root: Path) -> tuple[Path, ...]:
    _reject_tree_symlinks(root)
    return tuple(
        sorted(
            (
                candidate
                for candidate in root.rglob("*")
                if candidate.is_file()
            ),
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        )
    )


def _validated_generated_files(
    root: Path,
    files: Iterable[Path],
) -> tuple[Path, ...]:
    root_absolute = root.expanduser().absolute()
    by_relative_path: dict[str, Path] = {}
    for path in files:
        candidate = path.expanduser().absolute()
        try:
            relative = candidate.relative_to(root_absolute)
        except ValueError as exc:
            raise ValueError(
                f"generated export file is outside staging: {path}"
            ) from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(
                f"generated export path is not a regular file: {path}"
            )
        relative_text = relative.as_posix()
        if relative_text in by_relative_path:
            raise ValueError(
                f"duplicate generated export file: {relative_text}"
            )
        by_relative_path[relative_text] = candidate
    actual = {
        candidate.relative_to(root_absolute).as_posix()
        for candidate in _export_tree_files(root_absolute)
    }
    expected = set(by_relative_path)
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(
            "export staging tree differs from the explicit generated-file "
            f"allowlist: unexpected={unexpected}; missing={missing}"
        )
    return tuple(
        by_relative_path[relative]
        for relative in sorted(by_relative_path)
    )


def _fsync_export_tree(
    root: Path,
    *,
    files: Iterable[Path],
) -> None:
    generated = _validated_generated_files(root, files)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    for path in generated:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    directories = {
        root.expanduser().absolute(),
        *(
            parent
            for path in generated
            for parent in path.parents
            if parent == root.expanduser().absolute()
            or _path_is_within(parent, root.expanduser().absolute())
        ),
    }
    for directory in sorted(
        directories,
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_staged_export(
    staging_dir: Path,
    *,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not os.path.lexists(destination):
        os.replace(staging_dir, destination)
        _fsync_directory(destination.parent)
        return

    dropped = sorted(
        _relative_file_paths(destination)
        - _relative_file_paths(staging_dir)
    )
    if dropped:
        _record_dropped_export_paths(destination, dropped=dropped)
    _atomic_exchange_directories(staging_dir, destination)
    _fsync_directory(destination.parent)
    shutil.rmtree(staging_dir)
    _fsync_directory(destination.parent)


def _relative_file_paths(root: Path) -> set[str]:
    return {
        candidate.relative_to(root).as_posix()
        for candidate in root.rglob("*")
        if candidate.is_file()
    }


def _record_dropped_export_paths(
    destination: Path,
    *,
    dropped: list[str],
) -> None:
    log_path = destination.parent / (
        f"{destination.name}.export-dropped-paths.jsonl"
    )
    record = json.dumps(
        {
            "schema_version": "dual-agent-export-dropped-paths/v1",
            "ts": int(time.time()),
            "destination": str(destination),
            "dropped_paths": dropped,
        },
        sort_keys=True,
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(record + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(destination.parent)


def _atomic_exchange_directories(first: Path, second: Path) -> None:
    encoded_first = os.fsencode(first)
    encoded_second = os.fsencode(second)
    libc = ctypes.CDLL(None, use_errno=True)
    result: int | None = None
    if sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is not None:
            renamex_np.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renamex_np.restype = ctypes.c_int
            result = renamex_np(
                encoded_first,
                encoded_second,
                0x00000002,
            )
    elif sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100,
                encoded_first,
                -100,
                encoded_second,
                0x00000002,
            )
    if result == 0:
        return
    error_number = ctypes.get_errno() if result is not None else errno.ENOSYS
    raise OSError(
        error_number,
        "atomic directory exchange is unavailable; refusing a "
        "non-atomic export replacement",
        str(second),
    )


def default_dual_agent_artifact_dir(cwd: str | Path, task_id: str) -> Path:
    return Path(cwd).resolve() / "docs" / "dual-agent" / _safe_path_component(task_id)


def default_dual_agent_release_dir(cwd: str | Path, task_id: str) -> Path:
    """Return the clean-room package root, separate from live task artifacts."""
    return default_dual_agent_artifact_dir(cwd, task_id) / "release"


def _task_events_from_ledger_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    task_id: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload")
        if (
            not isinstance(payload, Mapping)
            or payload.get("task_id") != task_id
        ):
            continue
        events.append({
            "event_id": int(row["event_id"]),
            "ts": int(row["ts"]),
            "kind": str(row["kind"]),
            "gate": str(payload.get("gate") or "unknown"),
            "payload": dict(payload),
        })
    return events


def _read_run_ledger_rows(
    state: State,
    *,
    run_id: str,
) -> tuple[list[dict[str, Any]], int]:
    cut_event_id = state.latest_event_id(run_id)
    rows: list[dict[str, Any]] = []
    cursor = 0
    while cursor < cut_event_id:
        page = state.read_events_since(
            run_id,
            after_event_id=cursor,
            limit=1000,
        )
        if not page:
            break
        reached_cut = False
        for row in page:
            event_id = int(row["event_id"])
            if event_id > cut_event_id:
                reached_cut = True
                break
            rows.append(dict(row))
            cursor = event_id
        if reached_cut:
            break
    return rows, cut_event_id


def _ledger_export_manifest(
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    captured_head_event_id: int,
    path: str,
    authoritative_verification: Any,
) -> tuple[dict[str, Any], str]:
    export_rows = [
        {**row, "payload": redact(row.get("payload"))}
        for row in rows
    ]
    verification = verify_event_chain_structure(
        export_rows,
        expected_run_id=run_id,
    )
    ledger_text = "".join(
        f"{canonical_json_text(row)}\n"
        for row in export_rows
    )
    complete_capture = (
        verification.valid
        and verification.head_event_id == captured_head_event_id
    )
    failure_code = verification.failure_code
    failure_event_id = verification.failure_event_id
    detail = verification.detail
    if verification.valid and not complete_capture:
        failure_code = "captured_head_event_id_mismatch"
        failure_event_id = verification.head_event_id
        detail = (
            "full-read pagination did not reach the captured run head: "
            f"expected event_id={captured_head_event_id}, "
            f"observed={verification.head_event_id}"
        )
    authority_matches_capture = bool(
        complete_capture
        and getattr(authoritative_verification, "valid", False)
        and getattr(
            authoritative_verification,
            "authoritative_head_verified",
            False,
        )
        and getattr(authoritative_verification, "head_event_id", None)
        == verification.head_event_id
        and getattr(authoritative_verification, "head_event_hash", None)
        == verification.head_event_hash
        and getattr(
            authoritative_verification,
            "head_event_identity_hash",
            None,
        )
        == verification.head_event_identity_hash
    )
    authority_failure_code = getattr(
        authoritative_verification,
        "failure_code",
        None,
    )
    authority_detail = getattr(
        authoritative_verification,
        "detail",
        None,
    )
    if (
        not authority_matches_capture
        and getattr(authoritative_verification, "valid", False)
    ):
        authority_failure_code = "authoritative_capture_mismatch"
        authority_detail = (
            "authoritative ledger head differs from the exported capture"
        )
    return (
        {
            "schema_version": "dual-agent-evidence-ledger-export/v1",
            "status": (
                "verified_structural_prefix"
                if complete_capture
                else "invalid"
            ),
            "assurance": "structural_prefix_only",
            "authoritative_head_verified": authority_matches_capture,
            "authority_failure_code": (
                None if authority_matches_capture else authority_failure_code
            ),
            "authority_detail": (
                None if authority_matches_capture else authority_detail
            ),
            "external_anchor_ref": (
                getattr(
                    authoritative_verification,
                    "external_anchor_ref",
                    None,
                )
                if authority_matches_capture
                else None
            ),
            "scope": "run_genesis_through_captured_head",
            "encoding": "canonical-jsonl",
            "path": path,
            "sha256": sha256(
                ledger_text.encode("utf-8")
            ).hexdigest(),
            "run_id": run_id,
            "event_count": verification.event_count,
            "captured_head_event_id": captured_head_event_id,
            "head_event_id": verification.head_event_id,
            "head_event_hash": verification.head_event_hash,
            "head_event_identity_hash": (
                verification.head_event_identity_hash
            ),
            "failure_code": failure_code,
            "failure_event_id": failure_event_id,
            "detail": detail,
        },
        ledger_text,
    )


def _events_by_gate(events: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], ...]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["gate"], []).append(event)
    return {gate: tuple(items) for gate, items in grouped.items()}


def _has_accepted_gate_result(events: list[dict[str, Any]]) -> bool:
    for event in events:
        if event["kind"] != "dual_agent_gate_result":
            continue
        payload = event["payload"]
        for key in ("supervisor_final_status", "status", "claude_gate_status"):
            value = str(payload.get(key) or "").strip().lower()
            if value:
                if value in {"accept", "accepted"}:
                    return True
                break
    return False


def _has_trace_requiring_gate_result(
    events: list[dict[str, Any]],
) -> bool:
    return bool(_trace_requiring_source_events(events))


def _trace_requiring_source_events(
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        event
        for event in events
        if event["kind"] == "dual_agent_gate_result"
        and event["gate"] in {"execution", "outcome_review"}
        and str(
            event["payload"].get("status")
            or event["payload"].get("supervisor_final_status")
            or ""
        ).strip().lower() in {"accept", "accepted"}
    )


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
        "- [Canonical Evidence Ledger](replay/evidence-ledger.jsonl)",
        "- [Export Integrity](replay/export-integrity.json)",
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
        "- [Canonical Evidence Ledger](replay/evidence-ledger.jsonl)",
        "- [Export Integrity](replay/export-integrity.json)",
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


def _export_production_trace_records(
    events: list[dict[str, Any]],
    *,
    ledger_rows: list[dict[str, Any]],
    output_dir: Path,
    trusted_workspace_root: str | Path | None,
) -> tuple[dict[str, Any], tuple[Path, ...]]:
    schema_version = "dual-agent-production-trace-export/v1"
    recorded_events = [
        event
        for event in events
        if event["kind"] == "dual_agent_production_trace_recorded"
    ]
    failed_events = [
        event
        for event in events
        if event["kind"] == "dual_agent_production_trace_failed"
    ]
    required_source_event_ids = [
        int(event["event_id"])
        for event in _trace_requiring_source_events(events)
    ]
    if not recorded_events and not failed_events:
        missing_issues = ["no production trace event was exported"]
        missing_issues.extend(
            "required production trace authority is missing for "
            f"source event {source_event_id}"
            for source_event_id in required_source_event_ids
        )
        return (
            {
                "schema_version": schema_version,
                "status": (
                    "incomplete"
                    if required_source_event_ids
                    else "missing"
                ),
                "records": [],
                "failed_attempts": [],
                "issues": missing_issues,
            },
            (),
        )

    trusted_root = (
        Path(trusted_workspace_root).expanduser().resolve()
        if trusted_workspace_root is not None
        else None
    )
    allowed_root = (
        trusted_root
        / ".codex-supervisor"
        / "production-traces"
        if trusted_root is not None
        else None
    )
    records: list[dict[str, Any]] = []
    files: list[Path] = []
    ledger_by_event_id = {
        int(row["event_id"]): row
        for row in ledger_rows
    }
    issues: list[str] = []
    for event in recorded_events:
        event_id = int(event["event_id"])
        raw_receipt = event["payload"].get("receipt")
        (
            recorded_event_ref,
            source_event_ref,
            binding_issues,
        ) = _production_trace_ledger_binding(
            event,
            receipt=raw_receipt,
            ledger_by_event_id=ledger_by_event_id,
        )
        record_issues = list(binding_issues)
        if not isinstance(raw_receipt, Mapping):
            record_issues.append("event receipt is not an object")
            records.append({
                "event_id": event_id,
                "status": "incomplete",
                "binding_status": "invalid",
                "recorded_event": recorded_event_ref,
                "source_event": source_event_ref,
                "issues": record_issues,
            })
            issues.extend(
                f"event {event_id}: {issue}"
                for issue in record_issues
            )
            continue
        receipt = json.loads(json.dumps(raw_receipt, default=str))
        destination = (
            output_dir
            / "replay"
            / "production-traces"
            / str(event_id)
        )
        destination.mkdir(parents=True, exist_ok=True)
        public_artifacts: dict[str, dict[str, str]] = {}
        for label, receipt_path_key, receipt_hash_key, filename in (
            (
                "trace_store",
                "trace_store_path",
                "trace_store_sha256",
                "trace.db",
            ),
            (
                "gradebook",
                "gradebook_path",
                "gradebook_sha256",
                "grades.db",
            ),
        ):
            raw_path = str(receipt.get(receipt_path_key) or "").strip()
            expected_hash = str(
                receipt.get(receipt_hash_key) or ""
            ).strip().lower()
            if not raw_path or not re.fullmatch(
                r"[0-9a-f]{64}",
                expected_hash,
            ):
                record_issues.append(
                    f"{label} path or sha256 is missing"
                )
                continue
            source = Path(raw_path).expanduser().absolute()
            if (
                allowed_root is None
                or not _path_is_within(source, allowed_root)
            ):
                record_issues.append(
                    f"{label} is outside the trusted production-trace root"
                )
                continue
            try:
                content = _read_regular_path_no_follow(
                    source,
                    trusted_roots=(allowed_root,),
                )
            except (OSError, ValueError) as exc:
                record_issues.append(
                    f"{label} is not a trusted regular file: {exc}"
                )
                continue
            actual_hash = sha256(content).hexdigest()
            if actual_hash != expected_hash:
                record_issues.append(
                    f"{label} sha256 differs from the recorded receipt"
                )
                continue
            target = destination / filename
            written = target.write_bytes(content)
            copied_hash = sha256(content).hexdigest()
            if written != len(content):
                record_issues.append(
                    f"{label} copied byte count differs from the source"
                )
                target.unlink(missing_ok=True)
                continue
            if copied_hash != expected_hash:
                record_issues.append(
                    f"{label} copied bytes differ from the receipt"
                )
                target.unlink(missing_ok=True)
                continue
            files.append(target)
            public_artifacts[label] = {
                "path": target.relative_to(output_dir).as_posix(),
                "sha256": copied_hash,
            }

        public_receipt = {
            **receipt,
            "public_artifacts": public_artifacts,
        }
        authority_issues: list[str] = []
        if (
            not binding_issues
            and set(public_artifacts) == {"trace_store", "gradebook"}
        ):
            source_event_id = event["payload"].get("source_event_id")
            source_row = (
                ledger_by_event_id.get(source_event_id)
                if type(source_event_id) is int
                else None
            )
            if source_row is None:
                authority_issues.append(
                    "production trace authority verification failed: "
                    "canonical source event is unavailable"
                )
            else:
                authority_issues.extend(
                    _production_trace_authority_issues(
                        trace_store_path=(
                            output_dir
                            / public_artifacts["trace_store"]["path"]
                        ),
                        gradebook_path=(
                            output_dir
                            / public_artifacts["gradebook"]["path"]
                        ),
                        receipt=receipt,
                        source_row=source_row,
                    )
                )
        elif not binding_issues:
            authority_issues.append(
                "production trace authority verification failed: "
                "both exported authority databases are required"
            )
        record_issues.extend(authority_issues)
        receipt_path = destination / "receipt.json"
        receipt_text = json.dumps(
            public_receipt,
            indent=2,
            sort_keys=True,
        ) + "\n"
        receipt_path.write_text(receipt_text, encoding="utf-8")
        files.append(receipt_path)
        record = {
            "event_id": event_id,
            "source_event_id": event["payload"].get("source_event_id"),
            "source_event_hash": event["payload"].get(
                "source_event_hash"
            ),
            "binding_status": (
                "verified" if not binding_issues else "invalid"
            ),
            "authority_status": (
                "verified" if not authority_issues and not binding_issues
                else "invalid"
            ),
            "recorded_event": recorded_event_ref,
            "source_event": source_event_ref,
            "status": "complete" if not record_issues else "incomplete",
            "receipt_path": receipt_path.relative_to(
                output_dir
            ).as_posix(),
            "receipt_sha256": sha256(
                receipt_text.encode("utf-8")
            ).hexdigest(),
            "public_artifacts": public_artifacts,
            "issues": record_issues,
        }
        records.append(record)
        issues.extend(
            f"event {event_id}: {issue}"
            for issue in record_issues
        )
    failed_attempts: list[dict[str, Any]] = []
    recorded_by_event_id = {
        int(event["event_id"]): event
        for event in recorded_events
    }
    for failed_event in failed_events:
        failed_event_id = int(failed_event["event_id"])
        failed_identity = _production_trace_attempt_identity(
            failed_event
        )
        recovered_by = next(
            (
                int(record["event_id"])
                for record in records
                if record["status"] == "complete"
                and record.get("authority_status") == "verified"
                and int(record["event_id"]) > failed_event_id
                and _production_trace_attempt_identity(
                    recorded_by_event_id[int(record["event_id"])]
                ) == failed_identity
            ),
            None,
        )
        failed_attempts.append({
            "event_id": failed_event_id,
            "source_event_id": failed_event["payload"].get(
                "source_event_id"
            ),
            "source_event_hash": failed_event["payload"].get(
                "source_event_hash"
            ),
            "status": (
                "recovered"
                if recovered_by is not None
                else "blocking"
            ),
            "recovered_by_event_id": recovered_by,
        })
        if recovered_by is None:
            issues.append(
                "production trace recording failed for event "
                f"{failed_event_id}: "
                f"{failed_event['payload'].get('reason') or 'unknown'}"
            )
    complete_records_by_source: dict[int, list[int]] = {}
    for record in records:
        source_event_id = record.get("source_event_id")
        if (
            record.get("status") == "complete"
            and record.get("binding_status") == "verified"
            and record.get("authority_status") == "verified"
            and type(source_event_id) is int
        ):
            complete_records_by_source.setdefault(
                source_event_id,
                [],
            ).append(int(record["event_id"]))
    for source_event_id, record_event_ids in sorted(
        complete_records_by_source.items()
    ):
        if len(record_event_ids) != 1:
            issues.append(
                "production trace authority is not one-to-one for "
                f"source event {source_event_id}: "
                f"record events {record_event_ids}"
            )
    for source_event_id in required_source_event_ids:
        record_event_ids = complete_records_by_source.get(
            source_event_id,
            [],
        )
        if not record_event_ids:
            issues.append(
                "required production trace authority is missing for "
                f"source event {source_event_id}"
            )
    return (
        {
            "schema_version": schema_version,
            "status": (
                "complete"
                if records
                and not issues
                and all(
                    record["status"] == "complete"
                    for record in records
                )
                else "incomplete"
            ),
            "records": records,
            "failed_attempts": failed_attempts,
            "issues": issues,
        },
        tuple(files),
    )


def _production_trace_attempt_identity(
    event: Mapping[str, Any],
) -> tuple[Any, Any, Any, Any]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return (None, None, None, None)
    return (
        payload.get("task_id"),
        payload.get("gate"),
        payload.get("source_event_id"),
        payload.get("source_event_hash"),
    )


def _production_trace_ledger_binding(
    event: Mapping[str, Any],
    *,
    receipt: Any,
    ledger_by_event_id: Mapping[int, Mapping[str, Any]],
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[str],
]:
    issues: list[str] = []
    event_id = int(event["event_id"])
    recorded_row = ledger_by_event_id.get(event_id)
    recorded_ref = (
        _ledger_event_ref(recorded_row)
        if recorded_row is not None
        else None
    )
    if recorded_row is None:
        issues.append("recorded event is absent from the exported ledger")
    elif recorded_row.get("kind") != "dual_agent_production_trace_recorded":
        issues.append(
            "recorded event kind differs from the canonical ledger row"
        )
    elif recorded_row.get("payload") != event.get("payload"):
        issues.append(
            "recorded event payload differs from the canonical ledger row"
        )

    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return recorded_ref, None, [
            *issues,
            "recorded event payload is not an object",
        ]
    raw_source_event_id = payload.get("source_event_id")
    if (
        type(raw_source_event_id) is not int
        or raw_source_event_id <= 0
    ):
        return recorded_ref, None, [
            *issues,
            "source_event_id is not a positive canonical integer",
        ]
    source_event_id = raw_source_event_id
    source_row = ledger_by_event_id.get(source_event_id)
    source_ref = (
        _ledger_event_ref(source_row)
        if source_row is not None
        else None
    )
    if source_row is None:
        issues.append("source event is absent from the exported ledger")
        return recorded_ref, source_ref, issues

    source_event_hash = str(
        payload.get("source_event_hash") or ""
    ).strip()
    if source_row.get("event_hash") != source_event_hash:
        issues.append(
            "source_event_hash differs from the canonical ledger row"
        )
    if source_row.get("source") != "dual_agent":
        issues.append(
            "canonical source event source is not dual_agent"
        )
    if source_row.get("kind") != "dual_agent_gate_result":
        issues.append("source event is not a dual_agent_gate_result")
    if (
        recorded_row is not None
        and int(source_row["event_sequence"])
        >= int(recorded_row["event_sequence"])
    ):
        issues.append("source event does not precede the recorded event")

    source_payload = source_row.get("payload")
    if not isinstance(source_payload, Mapping):
        issues.append("source event payload is not an object")
    else:
        for field in ("task_id", "gate"):
            if source_payload.get(field) != payload.get(field):
                issues.append(
                    f"source event {field} differs from the recorded event"
                )

    if isinstance(receipt, Mapping):
        if str(receipt.get("source_event_id") or "") != str(
            source_event_id
        ):
            issues.append(
                "receipt source_event_id differs from the canonical source"
            )
        if receipt.get("source_event_hash") != source_row.get("event_hash"):
            issues.append(
                "receipt source_event_hash differs from the canonical source"
            )
        evidence = receipt.get("evidence")
        if not isinstance(evidence, Mapping):
            issues.append("receipt evidence is not an object")
        else:
            expected_values = {
                "run_id": source_row.get("run_id"),
                "task_id": payload.get("task_id"),
                "gate": payload.get("gate"),
                "source_event_id": str(source_event_id),
                "source_event_hash": source_row.get("event_hash"),
            }
            for field, expected in expected_values.items():
                if evidence.get(field) != expected:
                    issues.append(
                        f"receipt evidence {field} differs from the "
                        "canonical source"
                    )
            result_provenance = evidence.get("result_provenance")
            if (
                not isinstance(result_provenance, Mapping)
                or result_provenance.get("result_receipt_hash")
                != source_row.get("event_hash")
            ):
                issues.append(
                    "receipt result provenance does not pin the "
                    "canonical source event"
                )
    return recorded_ref, source_ref, issues


def _production_trace_authority_issues(
    *,
    trace_store_path: Path,
    gradebook_path: Path,
    receipt: Mapping[str, Any],
    source_row: Mapping[str, Any],
) -> list[str]:
    """Independently re-open and validate one exported trace authority."""
    try:
        if (
            receipt.get("trace_store_sha256")
            != sha256(trace_store_path.read_bytes()).hexdigest()
            or receipt.get("gradebook_sha256")
            != sha256(gradebook_path.read_bytes()).hexdigest()
        ):
            raise ValueError(
                "exported authority bytes differ from receipt hashes"
            )
        raw_evidence = receipt.get("evidence")
        if not isinstance(raw_evidence, Mapping):
            raise ValueError("receipt evidence is not an object")
        evidence = ProductionTraceEvidence(**dict(raw_evidence))
        expectation = _verify_production_trace_source_binding(
            evidence=evidence,
            source_row=source_row,
        )
        if (
            receipt.get("trace_store_path")
            != str(expectation.trace_store_path)
            or receipt.get("gradebook_path")
            != str(expectation.gradebook_path)
        ):
            raise ValueError(
                "persisted production trace store paths differ from the "
                "canonical gate workspace"
            )

        with tempfile.TemporaryDirectory(
            prefix="dual-agent-trace-verify-"
        ) as temp_dir:
            verification_root = Path(temp_dir)
            verification_trace = verification_root / "trace.db"
            verification_grades = verification_root / "grades.db"
            shutil.copyfile(trace_store_path, verification_trace)
            shutil.copyfile(gradebook_path, verification_grades)
            with TraceGraphStore(verification_trace) as store:
                graph = store.load()
            with GradeBook(verification_grades) as gradebook:
                _verify_production_trace_graph_authority(
                    graph=graph,
                    gradebook=gradebook,
                    evidence=evidence,
                    receipt=receipt,
                )
    except Exception as exc:
        return [
            "production trace authority verification failed: "
            f"{type(exc).__name__}: {exc}"
        ]
    return []


def _canonical_production_trace_expectation(
    source_row: Mapping[str, Any],
) -> _CanonicalProductionTraceExpectation:
    if source_row.get("source") != "dual_agent":
        raise ValueError(
            "canonical source event source is not dual_agent"
        )
    if source_row.get("kind") != "dual_agent_gate_result":
        raise ValueError(
            "canonical source event kind is not dual_agent_gate_result"
        )
    source_payload = source_row.get("payload")
    if not isinstance(source_payload, Mapping):
        raise ValueError("canonical source event payload is not an object")
    persisted_payload = dict(source_payload)
    source_event_hash = str(
        source_row.get("event_hash") or ""
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", source_event_hash):
        raise ValueError(
            "canonical source event lacks a canonical event hash"
        )

    raw_binding = persisted_payload.get("trace_closure_binding")
    if not isinstance(raw_binding, Mapping):
        raise ValueError(
            "canonical gate payload lacks trace_closure_binding"
        )
    planning_binding = TraceClosureBinding.from_mapping(raw_binding)
    if (
        planning_binding.run_id
        != str(source_row.get("run_id") or "")
        or str(persisted_payload.get("task_id") or "")
        != planning_binding.task_id
        or str(persisted_payload.get("gate") or "")
        != planning_binding.gate
    ):
        raise ValueError(
            "canonical gate identity differs from its trace closure binding"
        )

    target_run_registrations = list(
        persisted_payload.get("target_run_registrations") or []
    )
    if not all(
        isinstance(registration, Mapping)
        for registration in target_run_registrations
    ):
        raise ValueError(
            "canonical target runtime registrations must be objects"
        )
    runtime_calls = [
        call
        for call in (persisted_payload.get("tool_calls") or [])
        if isinstance(call, Mapping)
        and str(call.get("runtime_session_id") or "").strip()
    ]
    accepted = str(
        persisted_payload.get("status") or ""
    ).strip().lower() in {"accept", "accepted"}
    if accepted and not runtime_calls:
        raise ValueError(
            "accepted canonical gate payload lacks a concrete runtime call"
        )
    for runtime_call in runtime_calls:
        runtime_session_id = str(
            runtime_call.get("runtime_session_id") or ""
        ).strip()
        runtime_run_id = str(
            runtime_call.get("runtime_run_id") or ""
        ).strip()
        runtime_result_hash = str(
            runtime_call.get("runtime_result_hash") or ""
        ).strip()
        matching_registrations = [
            registration
            for registration in target_run_registrations
            if str(
                registration.get("target_session_id") or ""
            ).strip() == runtime_session_id
        ]
        if (
            len(matching_registrations) != 1
            or not runtime_run_id
            or not re.fullmatch(r"[0-9a-f]{64}", runtime_result_hash)
            or str(
                matching_registrations[0].get("runtime_run_id") or ""
            )
            != runtime_run_id
            or str(
                matching_registrations[0].get("runtime_result_hash") or ""
            )
            != runtime_result_hash
        ):
            raise ValueError(
                "canonical runtime call and registration provenance differ"
            )

    runtime_call = runtime_calls[-1] if runtime_calls else {}
    runtime_session_id = str(
        runtime_call.get("runtime_session_id") or ""
    ).strip()
    matching_registrations = [
        registration
        for registration in target_run_registrations
        if str(
            registration.get("target_session_id") or ""
        ).strip() == runtime_session_id
    ]
    if accepted and len(matching_registrations) != 1:
        raise ValueError(
            "accepted canonical runtime call lacks one exact workflow "
            "registration"
        )
    runtime_registration = (
        matching_registrations[0]
        if matching_registrations
        else {}
    )
    assignment_id = str(
        runtime_registration.get("target_run_id") or ""
    ).strip()
    if not assignment_id:
        assignment_id = (
            "workflow-gate:"
            + _production_trace_payload_sha256({
                "task_id": planning_binding.task_id,
                "run_id": planning_binding.run_id,
                "gate": planning_binding.gate,
                "source_event_hash": source_event_hash,
            })
        )
    runtime_provenance: dict[str, Any] = {
        "assignment_id": assignment_id,
        "arm": "supervisor",
        "runtime_kind": runtime_call.get("runtime"),
        "runtime_run_id": (
            str(runtime_call.get("runtime_run_id") or "").strip()
            or None
        ),
        "runtime_session_id": runtime_session_id or None,
        "runtime_result_hash": (
            str(runtime_call.get("runtime_result_hash") or "").strip()
            or None
        ),
        "model": runtime_call.get("model"),
        "attempts": int(persisted_payload.get("attempts") or 0),
        "runtime_calls": [dict(call) for call in runtime_calls],
        "target_run_registrations": target_run_registrations,
    }
    run_envelope_hash = _production_trace_payload_sha256({
        "schema_version": "supervisor-production-run-envelope/v1",
        "task_id": planning_binding.task_id,
        "run_id": planning_binding.run_id,
        "gate": planning_binding.gate,
        "source_event_hash": source_event_hash,
        "runtime_provenance": runtime_provenance,
    })
    runtime_provenance["run_envelope_hash"] = run_envelope_hash
    frozen_result_hash = _production_trace_payload_sha256(
        persisted_payload
    )
    task_hash = _production_trace_payload_sha256({
        "task_id": planning_binding.task_id,
        "run_id": planning_binding.run_id,
        "gate": planning_binding.gate,
        "planning_artifacts": [
            artifact.to_dict()
            for artifact in planning_binding.planning_artifacts
        ],
    })
    gate_hash = _production_trace_payload_sha256(
        planning_binding.to_dict()
    )

    raw_workspace_root = str(
        persisted_payload.get("production_trace_workspace_root") or ""
    ).strip()
    workspace_root = Path(raw_workspace_root).expanduser()
    if (
        not raw_workspace_root
        or not workspace_root.is_absolute()
        or str(workspace_root.resolve(strict=False)) != raw_workspace_root
    ):
        raise ValueError(
            "canonical gate payload lacks a canonical absolute "
            "production_trace_workspace_root"
        )
    trace_root = (
        workspace_root
        / ".codex-supervisor"
        / "production-traces"
        / source_event_hash
    )
    redacted_payload = redact(persisted_payload)
    evidence = ProductionTraceEvidence(
        task_id=planning_binding.task_id,
        task_hash=task_hash,
        run_id=planning_binding.run_id,
        run_envelope_hash=run_envelope_hash,
        frozen_result_hash=frozen_result_hash,
        gate=planning_binding.gate,
        gate_hash=gate_hash,
        planning_artifacts=planning_binding.planning_artifacts,
        runtime_provenance=runtime_provenance,
        result_provenance={
            "frozen_result_hash": frozen_result_hash,
            "result_kind": "dual_agent_gate_result",
            "result_receipt_hash": source_event_hash,
            "public_result": redacted_payload,
        },
        source_event_id=str(source_row.get("event_id") or ""),
        source_event_hash=source_event_hash,
        source_event_state="completed",
        source_event_recorded_at_ms=int(source_row["ts"]) * 1000,
        final_gate_result={
            "status": str(persisted_payload.get("status") or ""),
            "gate_result_hash": frozen_result_hash,
            "source_event_id": int(source_row["event_id"]),
            "source_event_hash": source_event_hash,
            "result": redacted_payload,
        },
    )
    return _CanonicalProductionTraceExpectation(
        evidence=evidence,
        trace_store_path=trace_root / "trace.db",
        gradebook_path=trace_root / "grades.db",
    )


def _verify_production_trace_source_binding(
    *,
    evidence: ProductionTraceEvidence,
    source_row: Mapping[str, Any],
) -> _CanonicalProductionTraceExpectation:
    expectation = _canonical_production_trace_expectation(source_row)
    if not _canonical_values_equal(
        evidence.to_dict(),
        expectation.evidence.to_dict(),
    ):
        raise ValueError(
            "persisted production trace evidence differs from the "
            "canonical gate payload"
        )
    return expectation


def _production_trace_payload_sha256(
    payload: Mapping[str, Any],
) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _verify_production_trace_graph_authority(
    *,
    graph: Any,
    gradebook: GradeBook,
    evidence: ProductionTraceEvidence,
    receipt: Mapping[str, Any],
) -> None:
    promotions = tuple(
        node
        for node in graph.nodes
        if node.identity.node_type is NodeType.PROMOTION
    )
    if len(promotions) != 1:
        raise ValueError(
            "trace store must contain exactly one promotion"
        )
    promotion = promotions[0]
    path = graph.promotion_trace(promotion.identity)
    expected_path = (
        NodeType.OBJ,
        NodeType.REQ,
        NodeType.TEST,
        NodeType.ASN,
        NodeType.RUN,
        NodeType.ART,
        NodeType.GRADE,
        NodeType.ANL,
        NodeType.DEC,
        NodeType.PROMOTION,
    )
    if tuple(node.identity.node_type for node in path) != expected_path:
        raise ValueError(
            "trace store lacks the canonical objective-to-promotion path"
        )
    if len(graph.nodes) != len(path):
        raise ValueError(
            "trace store contains records outside the authoritative path"
        )
    nodes = {
        node.identity.node_type: node
        for node in path
    }
    if len(nodes) != len(path):
        raise ValueError(
            "trace store contains duplicate authoritative node types"
        )

    binding = TraceClosureBinding(
        task_id=evidence.task_id,
        run_id=evidence.run_id,
        gate=evidence.gate,
        planning_artifacts=evidence.planning_artifacts,
    )
    closure = graph.validate_closure(
        now=datetime.fromtimestamp(
            evidence.source_event_recorded_at_ms / 1000,
            tz=timezone.utc,
        ),
        expected_binding=binding,
        decision_grade_validator=gradebook,
    )
    if not closure.ok:
        raise ValueError(
            "persisted production trace does not close: "
            + json.dumps(closure.to_dict(), sort_keys=True)
        )

    decision_node = nodes[NodeType.DEC]
    raw_citations = decision_node.attributes.get("grade_citations")
    if (
        not isinstance(raw_citations, (list, tuple))
        or len(raw_citations) != 1
        or not isinstance(raw_citations[0], Mapping)
    ):
        raise ValueError(
            "production trace decision must cite exactly one grade"
        )
    citation = DecisionGradeCitation.from_mapping(raw_citations[0])
    validation = gradebook.validate_decision((citation,))
    if not validation.accepted:
        raise ValueError(
            "production trace decision cites non-current grade authority"
        )
    revision = gradebook.get_revision(citation.grade_id)
    run = RunEnvelopeRef(
        run_id=evidence.run_id,
        run_envelope_hash=evidence.run_envelope_hash,
        frozen_result_hash=evidence.frozen_result_hash,
    )
    revisions = gradebook.list_revisions(run)
    if (
        not revisions
        or revisions[-1] != revision
        or citation.revision_hash != revision.revision_hash
    ):
        raise ValueError(
            "production trace citation is not the current grade revision"
        )
    terminal_commit = gradebook.get_terminal_commit(revision.grade_id)
    if terminal_commit is None:
        raise ValueError(
            "production trace current grade lacks a terminal commit"
        )
    if (
        terminal_commit.grade_revision_hash != revision.revision_hash
        or terminal_commit.task_id != evidence.task_id
        or terminal_commit.arm != evidence.arm
        or terminal_commit.terminal_state != evidence.source_event_state
        or terminal_commit.terminal_state_hash != evidence.source_event_hash
    ):
        raise ValueError(
            "production trace terminal commit differs from source authority"
        )

    grade_node = nodes[NodeType.GRADE]
    expected_grade_attributes = {
        "record_kind": "grade_revision",
        **revision.to_dict(),
        "terminal_commit": terminal_commit.to_dict(),
    }
    if (
        grade_node.identity.revision_hash != revision.revision_hash
        or not _canonical_values_equal(
            grade_node.attributes,
            expected_grade_attributes,
        )
    ):
        raise ValueError(
            "trace grade node differs from the authoritative GradeBook"
        )

    expected_grade_evidence = {
        "claim_cap": "L1",
        "hidden_outcome_evidence": False,
        "record_fingerprint": evidence.fingerprint,
        "production_trace_evidence": evidence.to_dict(),
        "source_event_id": evidence.source_event_id,
        "source_event_hash": evidence.source_event_hash,
        "source_event_state": evidence.source_event_state,
        "gate": evidence.gate,
        "gate_hash": evidence.gate_hash,
        "final_gate_result": dict(evidence.final_gate_result),
    }
    if not _canonical_values_equal(
        revision.evidence,
        expected_grade_evidence,
    ):
        raise ValueError(
            "current grade evidence differs from the production trace"
        )

    decision_id = (
        "production-trace:"
        + canonical_revision_hash({
            "run_id": evidence.run_id,
            "gate": evidence.gate,
            "source_event_hash": evidence.source_event_hash,
        })
    )
    decision = gradebook.get_decision(decision_id)
    if (
        decision.decision_hash
        != decision_node.attributes.get("decision_hash")
        or decision.grade_citations != (citation,)
    ):
        raise ValueError(
            "trace decision differs from the authoritative GradeBook"
        )
    expected_decision = {
        "claim_cap": "L1",
        "hidden_outcome_evidence": False,
        "record_fingerprint": evidence.fingerprint,
        "task_id": evidence.task_id,
        "task_hash": evidence.task_hash,
        "run_id": evidence.run_id,
        "run_envelope_hash": evidence.run_envelope_hash,
        "frozen_result_hash": evidence.frozen_result_hash,
        "gate": evidence.gate,
        "gate_hash": evidence.gate_hash,
        "planning_artifacts": [
            artifact.to_dict()
            for artifact in evidence.planning_artifacts
        ],
        "source_event_hash": evidence.source_event_hash,
        "final_gate_result": dict(evidence.final_gate_result),
    }
    if not _canonical_values_equal(
        decision.decision,
        expected_decision,
    ):
        raise ValueError(
            "persisted decision does not bind the exact trace evidence"
        )

    art_source = nodes[NodeType.ART].attributes.get("source_event")
    if (
        not isinstance(art_source, Mapping)
        or art_source.get("event_id") != evidence.source_event_id
        or art_source.get("event_hash") != evidence.source_event_hash
        or art_source.get("state") != evidence.source_event_state
    ):
        raise ValueError(
            "trace artifact node does not bind the exact source event"
        )
    if (
        promotion.attributes.get("record_fingerprint")
        != evidence.fingerprint
        or promotion.attributes.get("source_event_hash")
        != evidence.source_event_hash
        or promotion.attributes.get("gate_hash") != evidence.gate_hash
    ):
        raise ValueError(
            "promotion does not bind the exact production trace fingerprint"
        )

    receipt_checks = {
        "schema_version": PRODUCTION_TRACE_RECEIPT_SCHEMA_VERSION,
        "claim_cap": "L1",
        "record_fingerprint": evidence.fingerprint,
        "trace_graph_hash": sha256(graph.canonical_bytes()).hexdigest(),
        "source_event_id": evidence.source_event_id,
        "source_event_hash": evidence.source_event_hash,
        "grade_terminal_commit_hash": terminal_commit.commit_hash,
        "verifier_config_hash": revision.verifier_config_hash,
        "verifier_implementation_hash": (
            revision.verifier_implementation_hash
        ),
    }
    for field, expected in receipt_checks.items():
        if receipt.get(field) != expected:
            raise ValueError(
                f"receipt {field} differs from persisted authority"
            )
    exact_receipt_objects = {
        "closure": closure.to_dict(),
        "promotion": promotion.identity.to_dict(),
        "grade_citation": citation.to_dict(),
        "trace_graph": graph.to_dict(),
        "grade_revision": revision.to_dict(),
        "grade_terminal_commit": terminal_commit.to_dict(),
        "grade_decision": decision.to_dict(),
    }
    for field, expected in exact_receipt_objects.items():
        if not _canonical_values_equal(receipt.get(field), expected):
            raise ValueError(
                f"receipt {field} differs from persisted authority"
            )


def _canonical_values_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(
            _plain_json_value(left)
        ) == canonical_json_bytes(_plain_json_value(right))
    except (TypeError, ValueError):
        return False


def _plain_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_plain_json_value(item) for item in value]
    return value


def _ledger_event_ref(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": int(row["event_id"]),
        "event_sequence": int(row["event_sequence"]),
        "event_hash": row["event_hash"],
        "kind": row["kind"],
    }


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(
            root.expanduser().resolve()
        )
    except ValueError:
        return False
    return True


def _write_export_integrity(
    output_dir: Path,
    *,
    path: Path,
    ledger_manifest: Mapping[str, Any],
    generated_files: Iterable[Path],
) -> dict[str, Any]:
    output_root = output_dir.expanduser().absolute()
    integrity_path = path.expanduser().absolute()
    validated_files = _validated_generated_files(
        output_root,
        generated_files,
    )
    files = [
        {
            "path": candidate.relative_to(output_root).as_posix(),
            "size": len(content),
            "sha256": sha256(content).hexdigest(),
        }
        for candidate in validated_files
        for content in (candidate.read_bytes(),)
    ]
    file_tree = {
        "schema_version": "dual-agent-public-export-file-tree/v1",
        "files": files,
    }
    body = {
        "schema_version": "dual-agent-public-export-integrity/v1",
        "hash_algorithm": "sha256",
        "integrity_path": integrity_path.relative_to(
            output_root
        ).as_posix(),
        "file_tree_schema_version": file_tree["schema_version"],
        "file_tree_scope": (
            "explicit_generated_files_except_integrity_document"
        ),
        "file_tree_sha256": sha256(
            canonical_json_bytes(file_tree)
        ).hexdigest(),
        "files": files,
        "ledger": dict(ledger_manifest),
    }
    integrity = {
        **body,
        "export_root_sha256": sha256(
            canonical_json_bytes(body)
        ).hexdigest(),
    }
    integrity_path.write_text(
        json.dumps(integrity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return integrity


def _replay_manifest(
    *,
    run_id: str,
    task_id: str,
    events: list[dict[str, Any]],
    transcript_jsonl: str,
    workspace_snapshot: dict[str, Any],
    mast_coverage: list[dict[str, Any]],
    provider_model_resolutions: Iterable[dict[str, Any]] = (),
    canonical_tool_contracts: Iterable[dict[str, Any]] = (),
    runtime_component_receipts: Iterable[dict[str, Any]] = (),
    production_trace_manifest: Mapping[str, Any] | None = None,
    ledger_manifest: Mapping[str, Any] | None = None,
    preserved_artifacts: Iterable[Mapping[str, Any]] = (),
    unresolved_artifacts: Iterable[Mapping[str, Any]] = (),
    trusted_workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    event_ids = [int(event["event_id"]) for event in events]
    handoff_packets = _handoff_packet_manifest(
        events,
        trusted_workspace_root=trusted_workspace_root,
    )
    execution_provenance = build_execution_provenance(
        events=events,
        workspace_snapshot=workspace_snapshot,
        handoff_packets=handoff_packets,
        provider_model_resolutions=provider_model_resolutions,
        canonical_tool_contracts=canonical_tool_contracts,
        runtime_component_receipts=runtime_component_receipts,
    )
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
            "execution_provenance": EXECUTION_PROVENANCE_SCHEMA_VERSION,
            "production_trace_export": (
                "dual-agent-production-trace-export/v1"
            ),
        },
        "files": {
            "index": "index.md",
            "interactions": "interactions.md",
            "transcript_markdown": "transcript.md",
            "transcript_jsonl": "transcript.jsonl",
            "mast_coverage_markdown": "mast-coverage.md",
            "mast_coverage_json": "replay/mast-coverage.json",
            "workspace_snapshot": "replay/workspace-snapshot.json",
            "production_traces": "replay/production-traces/",
            "evidence_ledger": "replay/evidence-ledger.jsonl",
            "export_integrity": "replay/export-integrity.json",
        },
        "event_kinds": sorted({str(event["kind"]) for event in events}),
        "handoff_packets": handoff_packets,
        "workspace_snapshot": workspace_snapshot,
        "execution_provenance": execution_provenance,
        "production_trace": dict(
            production_trace_manifest
            or {
                "schema_version": (
                    "dual-agent-production-trace-export/v1"
                ),
                "status": "missing",
                "records": [],
                "failed_attempts": [],
                "issues": ["no production trace event was exported"],
            }
        ),
        "preserved_artifacts": [
            dict(descriptor)
            for descriptor in preserved_artifacts
        ],
        "unresolved_artifacts": [
            dict(descriptor)
            for descriptor in unresolved_artifacts
        ],
        "ledger": dict(
            ledger_manifest
            or {
                "schema_version": (
                    "dual-agent-evidence-ledger-export/v1"
                ),
                "status": "missing",
                "assurance": "structural_prefix_only",
                "authoritative_head_verified": False,
                "scope": "run_genesis_through_captured_head",
                "encoding": "canonical-jsonl",
                "path": "replay/evidence-ledger.jsonl",
                "sha256": "",
                "run_id": run_id,
                "event_count": 0,
                "captured_head_event_id": None,
                "head_event_id": None,
                "head_event_hash": None,
                "head_event_identity_hash": None,
                "failure_code": "missing",
                "failure_event_id": None,
                "detail": "canonical ledger rows were not exported",
            }
        ),
        "model_resolutions": execution_provenance["model_resolutions"],
        "component_hashes": execution_provenance["component_hashes"],
        "failure_summary": _run_failure_summary(events),
        "sequence_failures": detect_sequence_failures(events),
        "mast_coverage": mast_coverage,
        "tool_call_totals": _tool_call_totals(events),
    }


def _handoff_packet_manifest(
    events: list[dict[str, Any]],
    *,
    trusted_workspace_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    by_path: dict[str, list[int]] = {}
    captured_by_path: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event["payload"]
        path = payload.get("handoff_packet_path")
        if not path:
            continue
        path_text = str(path)
        by_path.setdefault(path_text, []).append(int(event["event_id"]))
        acceptance = _load_acceptance_snapshot(
            payload,
            trusted_workspace_root=trusted_workspace_root,
        )
        packet = (
            acceptance.get("handoff_packet")
            if isinstance(acceptance, dict)
            and isinstance(acceptance.get("handoff_packet"), dict)
            else None
        )
        if packet is not None and str(packet.get("path") or "") == path_text:
            captured_by_path[path_text] = dict(packet)

    packets: list[dict[str, Any]] = []
    for path_text, event_ids in sorted(by_path.items()):
        captured = captured_by_path.get(path_text)
        if captured is not None:
            packets.append({
                **captured,
                "path": path_text,
                "event_ids": event_ids,
                "capture_source": "accepted_gate_event",
            })
            continue
        item: dict[str, Any] = {
            "path": path_text,
            "event_ids": event_ids,
            "capture_source": "posthoc_diagnostic",
        }
        content_bytes, read_status = _read_posthoc_file(
            path_text,
            trusted_workspace_root=trusted_workspace_root,
        )
        if content_bytes is not None:
            content = content_bytes.decode("utf-8", errors="replace")
            item.update({
                "status": "captured",
                "sha256": sha256(content.encode()).hexdigest(),
                "content": content,
            })
        else:
            item.update({
                "status": read_status,
                "sha256": None,
                "content": None,
            })
        packets.append(item)
    return packets


def _workspace_snapshot_manifest(
    events: list[dict[str, Any]],
    *,
    output_dir: Path,
    trusted_workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    for event in reversed(events):
        payload = event["payload"]
        raw_acceptance = payload.get("acceptance_evidence")
        acceptance = _load_acceptance_snapshot(
            payload,
            trusted_workspace_root=trusted_workspace_root,
        )
        snapshot = (
            acceptance.get("workspace_snapshot")
            if isinstance(acceptance, dict)
            and isinstance(acceptance.get("workspace_snapshot"), dict)
            else None
        )
        if snapshot is not None:
            return dict(snapshot)
        if isinstance(raw_acceptance, dict):
            return {
                "status": "acceptance_snapshot_invalid",
                "capture_source": "accepted_gate_event",
                "snapshot_ref": raw_acceptance.get("snapshot_ref"),
            }

    handoff, handoff_status = _first_handoff_content(
        events,
        trusted_workspace_root=trusted_workspace_root,
    )
    root_text = _clean_text(handoff.get("cwd")) if isinstance(handoff, dict) else ""
    if not root_text:
        if handoff_status == "outside_trusted_workspace":
            return {
                "status": "posthoc_capture_blocked",
                "capture_source": "posthoc_diagnostic",
                "reason": "handoff_outside_trusted_workspace",
            }
        return {
            "status": "not_found",
            "capture_source": "posthoc_diagnostic",
            "reason": "handoff_cwd_missing",
        }
    root = Path(root_text).expanduser().absolute()
    trusted_root = _normalized_trusted_workspace_root(
        trusted_workspace_root
    )
    if trusted_root is not None and not _path_is_within(root, trusted_root):
        return {
            "status": "posthoc_capture_blocked",
            "capture_source": "posthoc_diagnostic",
            "reason": "handoff_cwd_outside_trusted_workspace",
        }
    if not root.exists() or not root.is_dir():
        return {
            "status": "missing_at_export",
            "capture_source": "posthoc_diagnostic",
            "root": root_text,
        }

    status_short = _run_git(root, "status", "--short")
    diff = _run_git(root, "diff", "--no-ext-diff") or ""
    head = _run_git(root, "rev-parse", "HEAD")
    diff_stat = _run_git(root, "diff", "--stat", "--no-ext-diff")
    excluded_roots = _workspace_snapshot_excluded_roots(
        root,
        output_dir=output_dir,
    )
    source_artifact_paths = _source_artifact_paths(root, handoff)
    immutable_snapshot = build_workspace_overlay(
        root,
        base_commit=head,
        excluded_roots=excluded_roots,
        included_paths=source_artifact_paths,
    )
    return {
        "status": "captured",
        "root": str(root),
        "root_source": "handoff_cwd",
        "capture_source": "posthoc_diagnostic",
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
        "file_tree_sha256": _file_tree_sha256(
            root,
            excluded_roots=excluded_roots,
        ),
        "source_artifact_hashes": _source_artifact_hashes(root, handoff),
        "immutable_snapshot": immutable_snapshot,
    }


def _load_acceptance_snapshot(
    payload: dict[str, Any],
    *,
    trusted_workspace_root: str | Path | None = None,
) -> dict[str, Any] | None:
    acceptance = payload.get("acceptance_evidence")
    if not isinstance(acceptance, dict):
        return None
    if (
        isinstance(acceptance.get("handoff_packet"), dict)
        and isinstance(acceptance.get("workspace_snapshot"), dict)
    ):
        return acceptance
    snapshot_ref = str(acceptance.get("snapshot_ref") or "").strip()
    expected_sha256 = str(
        acceptance.get("snapshot_sha256") or ""
    ).strip().lower().removeprefix("sha256:")
    if not snapshot_ref or len(expected_sha256) != 64:
        return None
    content, _read_status = _read_posthoc_file(
        snapshot_ref,
        trusted_workspace_root=trusted_workspace_root,
        expected_sha256=expected_sha256,
    )
    if content is None:
        return None
    if sha256(content).hexdigest() != expected_sha256:
        return None
    try:
        snapshot = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version")
        != "dual-agent-acceptance-snapshot/v1"
    ):
        return None
    return snapshot


def _first_handoff_content(
    events: list[dict[str, Any]],
    *,
    trusted_workspace_root: str | Path | None = None,
) -> tuple[dict[str, Any], str | None]:
    last_status: str | None = None
    for event in events:
        path_text = _clean_text(event["payload"].get("handoff_packet_path"))
        if not path_text:
            continue
        content, last_status = _read_posthoc_file(
            path_text,
            trusted_workspace_root=trusted_workspace_root,
        )
        if content is None:
            continue
        try:
            payload = strict_json_object_loads(
                content.decode("utf-8", errors="replace") or "{}"
            )
        except (TypeError, UnicodeDecodeError, ValueError):
            last_status = "invalid_at_export"
            continue
        return payload, None
    return {}, last_status


def _normalized_trusted_workspace_root(
    trusted_workspace_root: str | Path | None,
) -> Path | None:
    if trusted_workspace_root is None:
        return None
    return Path(trusted_workspace_root).expanduser().absolute()


def _read_posthoc_file(
    path_text: str,
    *,
    trusted_workspace_root: str | Path | None,
    expected_sha256: str | None = None,
) -> tuple[bytes | None, str]:
    path = Path(path_text).expanduser()
    trusted_root = _normalized_trusted_workspace_root(
        trusted_workspace_root
    )
    if trusted_root is not None:
        candidate = (
            path.absolute()
            if path.is_absolute()
            else (trusted_root / path).absolute()
        )
        if not _path_is_within(candidate, trusted_root):
            return None, "outside_trusted_workspace"
        roots = (trusted_root,)
    elif expected_sha256 is not None:
        candidate = path.absolute()
        roots = (candidate.parent,)
    else:
        return None, "outside_trusted_workspace"
    try:
        content = _read_regular_path_no_follow(
            candidate,
            trusted_roots=roots,
        )
    except FileNotFoundError:
        return None, "missing_at_export"
    except (OSError, ValueError):
        return None, "untrusted_or_unreadable_at_export"
    if (
        expected_sha256 is not None
        and sha256(content).hexdigest() != expected_sha256
    ):
        return None, "untrusted_or_unreadable_at_export"
    return content, "captured"


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


def _workspace_snapshot_excluded_roots(
    root: Path,
    *,
    output_dir: Path,
) -> tuple[Path, ...]:
    try:
        relative = output_dir.expanduser().resolve().relative_to(
            root.expanduser().resolve()
        )
    except (OSError, ValueError):
        return ()
    return (relative,) if relative.parts else ()


def _file_tree_sha256(
    root: Path,
    *,
    excluded_roots: tuple[Path, ...] = (),
) -> str:
    digest = sha256()
    for path in _snapshot_file_paths(root):
        if not path.is_file() or _excluded_snapshot_path(
            path,
            root,
            excluded_roots=excluded_roots,
        ):
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


def _source_artifact_paths(
    root: Path,
    handoff: dict[str, Any],
) -> tuple[str, ...]:
    artifacts = (
        handoff.get("planning_artifacts")
        if isinstance(handoff, dict)
        else []
    )
    if not isinstance(artifacts, list):
        return ()
    root_resolved = root.expanduser().resolve()
    paths: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        path_text = _clean_text(artifact.get("path"))
        if not path_text or Path(path_text).is_absolute():
            continue
        try:
            (root_resolved / path_text).resolve().relative_to(
                root_resolved
            )
        except (OSError, ValueError):
            continue
        paths.append(path_text)
    return tuple(paths)


def _excluded_snapshot_path(
    path: Path,
    root: Path,
    *,
    excluded_roots: tuple[Path, ...] = (),
) -> bool:
    rel = path.relative_to(root).as_posix()
    relative = Path(rel)
    if any(
        relative == excluded or excluded in relative.parents
        for excluded in excluded_roots
    ):
        return True
    parts = set(relative.parts)
    if parts & {
        ".git",
        ".venv",
        ".claude",
        ".codex-supervisor",
        ".cortex",
        ".handoff",
        ".orchestrator-state",
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
            "- interaction_type: `round`",
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

    if event["kind"] == "independent_reviewer_review":
        return _independent_reviewer_review_event_markdown(
            heading=f"## {index}. {title}",
            event=event,
            include_kind=False,
        )

    if event["kind"] == "independent_reviewer_adjudication":
        return _independent_reviewer_adjudication_event_markdown(
            heading=f"## {index}. {title}",
            event=event,
            include_kind=False,
        )

    if event["kind"] == "tri_agent_cursor_review":
        return _cursor_review_event_markdown(
            heading=f"## {index}. {title}",
            event=event,
            include_kind=False,
        )

    if event["kind"] == "dual_agent_reviewer_unavailable_recovery":
        return _reviewer_unavailable_recovery_event_markdown(
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
            "- interaction_type: `gate_result`",
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
        "- interaction_type: `gate_result`",
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

    if event["kind"] == "independent_reviewer_review":
        return _independent_reviewer_review_event_markdown(
            heading=f"## event_id: {event['event_id']}",
            event=event,
            include_kind=True,
        )

    if event["kind"] == "independent_reviewer_adjudication":
        return _independent_reviewer_adjudication_event_markdown(
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

    if event["kind"] == "dual_agent_reviewer_unavailable_recovery":
        return _reviewer_unavailable_recovery_event_markdown(
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
    recovery = cursor_review.get("reviewer_unavailable_recovery")
    if isinstance(recovery, dict):
        lines.extend([
            "### Reviewer Unavailable Recovery",
            "",
            f"- decision: `{_clean_text(recovery.get('decision'))}`",
            f"- policy: `{_clean_text(recovery.get('policy'))}`",
            f"- evidence_grade: `{_clean_text(recovery.get('evidence_grade'))}`",
            f"- reviewer_verdict_counted_as_accept: `{recovery.get('reviewer_verdict_counted_as_accept')}`",
            f"- forced_by_safety: `{recovery.get('forced_by_safety')}`",
            "",
        ])
    lines.extend(_trace_envelope_section(payload))
    return "\n".join(lines)


def _independent_reviewer_review_event_markdown(
    *,
    heading: str,
    event: dict[str, Any],
    include_kind: bool,
) -> str:
    payload = event["payload"]
    results = payload.get("independent_reviewer_results")
    if not isinstance(results, list):
        results = []
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
        "- interaction_type: `independent_reviewer_review`",
        f"- gate: `{event['gate']}`",
        f"- reviewer_count: `{len(results)}`",
        "",
        "### Independent Reviewer Results",
        "",
    ])
    if not results:
        lines.extend(["- None recorded.", ""])
    for index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            continue
        lines.extend([
            f"#### Reviewer {index}: `{_clean_text(result.get('reviewer_id'))}`",
            "",
            f"- accepted: `{result.get('accepted')}`",
            f"- decision: `{_clean_text(result.get('decision'))}`",
            f"- severity: `{_clean_text(result.get('severity'))}`",
            f"- confidence: `{_clean_text(result.get('confidence'))}`",
            f"- runtime: `{_clean_text(result.get('runtime') or result.get('reviewer_runtime'))}`",
            f"- model: `{_clean_text(result.get('model'))}`",
            f"- provider_family: `{_clean_text(result.get('provider_family'))}`",
            f"- lineage: {_inline_markdown_value(result.get('lineage') or [])}",
            f"- tool_access: `{_clean_text(result.get('tool_access'))}`",
            f"- assurance_grade: `{_clean_text(result.get('assurance_grade'))}`",
            f"- transcript_sha256: `{_clean_text(result.get('transcript_sha256'))}`",
            f"- output_sha256: `{_clean_text(result.get('output_sha256'))}`",
            "",
            "Transcript refs:",
            "",
            _list_markdown(result.get("transcript_refs")),
            "",
            "Critical review:",
            "",
            _inline_markdown_value(result.get("critical_review") or {}),
            "",
        ])
    lines.extend(_trace_envelope_section(payload))
    return "\n".join(lines)


def _independent_reviewer_adjudication_event_markdown(
    *,
    heading: str,
    event: dict[str, Any],
    include_kind: bool,
) -> str:
    payload = event["payload"]
    adjudication = payload.get("adjudication") if isinstance(payload.get("adjudication"), dict) else {}
    strongest = (
        adjudication.get("strongest_objection")
        if isinstance(adjudication.get("strongest_objection"), dict)
        else {}
    )
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
        "- interaction_type: `independent_reviewer_adjudication`",
        f"- gate: `{event['gate']}`",
        f"- trigger: `{_clean_text(adjudication.get('trigger'))}`",
        f"- decision: `{_clean_text(adjudication.get('decision'))}`",
        f"- reason: `{_clean_text(adjudication.get('reason'))}`",
        f"- majority_vote_used: `{adjudication.get('majority_vote_used')}`",
        "",
        "### Strongest Objection",
        "",
        f"- reviewer_id: `{_clean_text(strongest.get('reviewer_id'))}`",
        f"- decision: `{_clean_text(strongest.get('decision'))}`",
        f"- severity: `{_clean_text(strongest.get('severity'))}`",
        f"- confidence: `{_clean_text(strongest.get('confidence'))}`",
        f"- text: {_text_or_none(strongest.get('text'))}",
        f"- transcript_sha256: `{_clean_text(strongest.get('transcript_sha256'))}`",
        f"- output_sha256: `{_clean_text(strongest.get('output_sha256'))}`",
        "",
        "Evidence refs:",
        "",
        _list_markdown(strongest.get("evidence_refs")),
        "",
        "Tests:",
        "",
        _list_markdown(strongest.get("tests")),
        "",
        "Evidence checks:",
        "",
        _inline_markdown_value(adjudication.get("evidence_checks") or []),
        "",
    ])
    lines.extend(_trace_envelope_section(payload))
    return "\n".join(lines)


def _reviewer_unavailable_recovery_event_markdown(
    *,
    heading: str,
    event: dict[str, Any],
    include_kind: bool,
) -> str:
    payload = event["payload"]
    recovery = payload.get("recovery") if isinstance(payload.get("recovery"), dict) else {}
    authorization = payload.get("authorization") if isinstance(payload.get("authorization"), dict) else {}
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
        "- interaction_type: `reviewer_unavailable_recovery`",
        f"- gate: `{event['gate']}`",
        f"- status: `{_clean_text(payload.get('status'))}`",
        f"- policy: `{_clean_text(payload.get('policy'))}`",
        f"- classification: `{_clean_text(payload.get('classification'))}`",
        f"- evidence_grade: `{_clean_text(payload.get('evidence_grade'))}`",
        f"- reviewer_verdict_counted_as_accept: `{payload.get('reviewer_verdict_counted_as_accept')}`",
        f"- forced_by_safety: `{payload.get('forced_by_safety')}`",
        "",
        "### Available Reviewers",
        "",
        _inline_markdown_value(payload.get("available_reviewers") or {}),
        "",
        "### Safety Reasons",
        "",
        _list_markdown(payload.get("safety_reasons")),
        "",
        "### Recovery Decision",
        "",
        f"- decision: `{_clean_text(recovery.get('decision') or payload.get('decision'))}`",
        f"- reason: `{_clean_text(recovery.get('reason'))}`",
        "",
    ])
    if authorization:
        lines.extend([
            "### Authorization",
            "",
            _inline_markdown_value(authorization),
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
    latest_gate_result = _latest_gate_result_event(events)
    if latest_gate_result is not None:
        payload = latest_gate_result.get("payload") if isinstance(latest_gate_result.get("payload"), dict) else {}
        envelope = payload.get("trace_envelope") if isinstance(payload.get("trace_envelope"), dict) else {}
        verdict = _clean_text(envelope.get("policy_verdict"))
        failure = envelope.get("failure_taxonomy")
        status = _clean_text(payload.get("status"))
        supervisor_status = _clean_text(payload.get("supervisor_final_status") or status)
        if (
            status == "accepted"
            and supervisor_status == "accepted"
            and verdict != "blocked"
            and not isinstance(failure, dict)
        ):
            return None
        if verdict == "blocked" or isinstance(failure, dict):
            return {
                "event_id": int(latest_gate_result["event_id"]),
                "policy_verdict": verdict,
                "failure_taxonomy": failure,
            }

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
    *,
    trusted_workspace_root: str | Path | None,
) -> list[tuple[Path, ScreenshotArtifact]]:
    copied: list[tuple[Path, ScreenshotArtifact]] = []
    if not screenshots:
        return copied
    if trusted_workspace_root is None:
        raise ValueError(
            "screenshot export requires an explicit trusted workspace root"
        )
    allowed_root = Path(
        trusted_workspace_root
    ).expanduser().absolute()
    _reject_symlink_components(allowed_root)
    screenshot_dir = output_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    for index, screenshot in enumerate(screenshots, start=1):
        source = Path(screenshot.path).expanduser().absolute()
        try:
            source.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError(
                "screenshot artifact is outside the trusted workspace root"
            ) from exc
        try:
            content = _read_regular_path_no_follow(
                source,
                trusted_roots=(allowed_root,),
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                "screenshot source contains a symlink or is not a trusted "
                "regular file"
            ) from exc
        label = _clean_text(screenshot.label) or f"Screenshot {index}"
        suffix = source.suffix if source.suffix else ".png"
        filename = f"{index:02d}-{_safe_path_component(label).lower()}{suffix}"
        target = screenshot_dir / filename
        target.write_bytes(content)
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
