"""Workflow-to-target run registration and sidecar joins.

The workflow run id is the supervisor's durable identity. The target session
id is the rollout filename identity. A session-keyed sidecar joins the two
before target events arrive, and State.register_run co-registers the workflow
run so active-run monitoring and drift use the same id as the workflow ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, Mapping
import uuid

from .state import State
from .target.types import ScopeContract


RUN_REGISTRATION_SCHEMA = "supervisor-run-registration/v1"
LAUNCH_RECEIPT_SCHEMA = "supervisor-launch-receipt/v1"
PENDING_SESSION_SOURCE = "pending_runtime_receipt"
LAUNCH_RECEIPT_SOURCE = "launch_receipt"
DEFAULT_LAUNCH_RECEIPT_TTL_S = 3_600
_LAUNCH_RECEIPT_DIRNAME = ".launch-receipts"


class LaunchReceiptError(ValueError):
    """A launch receipt was absent, stale, replayed, or did not match."""


@dataclass(frozen=True)
class WorkflowRunRegistration:
    workflow_run_id: str
    target_session_id: str
    task_id: str
    task: str
    target_kind: str
    registry_path: Path
    session_id_source: str

    def event_payload(self, *, job_id: str | None = None) -> dict[str, Any]:
        pending = self.session_id_source == PENDING_SESSION_SOURCE
        payload: dict[str, Any] = {
            "schema_version": RUN_REGISTRATION_SCHEMA,
            "workflow_run_id": self.workflow_run_id,
            "target_session_id": self.target_session_id or None,
            "task_id": self.task_id,
            "task": self.task,
            "target_kind": self.target_kind,
            "join_key": self.target_session_id or None,
            "session_id_source": self.session_id_source,
            "registry_path": str(self.registry_path),
            "pending": pending,
        }
        if job_id:
            payload["job_id"] = job_id
        return payload


@dataclass(frozen=True)
class LaunchReceipt:
    launch_id: str
    nonce: str
    workflow_run_id: str
    task_id: str
    target_kind: str
    cwd: str
    issued_at: int
    expires_at: int
    receipt_path: Path

    def launch_environment(self) -> dict[str, str]:
        """Return the one-launch credentials passed only to the target process."""
        return {
            "SUPERVISOR_LAUNCH_ID": self.launch_id,
            "SUPERVISOR_LAUNCH_NONCE": self.nonce,
            "SUPERVISOR_WORKFLOW_RUN_ID": self.workflow_run_id,
            "SUPERVISOR_WORKFLOW_TASK_ID": self.task_id,
            "SUPERVISOR_TARGET_KIND": self.target_kind,
        }


def resolve_target_session_id(
    *,
    workflow_run_id: str,
    target_kind: str,
    explicit_session_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Resolve the target session id available at workflow submission."""
    explicit = str(explicit_session_id or "").strip()
    if explicit:
        return explicit, "explicit"

    # Ambient session variables describe the caller (often Codex Desktop), not
    # the target process that the supervisor is about to launch. Treating them
    # as a join key lets an unrelated same-cwd rollout capture the workflow.
    # The actual target session is bound only after its launch receipt is
    # atomically consumed.
    _ = (workflow_run_id, target_kind, environ)
    return "", PENDING_SESSION_SOURCE


def register_submitted_workflow(
    *,
    state: State,
    registry_dir: str | Path,
    workflow_run_id: str,
    target_session_id: str,
    task_id: str,
    task: str,
    target_kind: str,
    cwd: str | Path,
    session_id_source: str,
    scope_contract: ScopeContract | None = None,
) -> WorkflowRunRegistration:
    """Co-register a workflow run and atomically write its session join."""
    registry_root = Path(registry_dir).expanduser().resolve()
    registry_root.mkdir(parents=True, exist_ok=True)
    raw_target_session_id = str(target_session_id).strip()
    pending = session_id_source == PENDING_SESSION_SOURCE
    if pending:
        if raw_target_session_id:
            raise ValueError(
                "pending target session registration must not include a session id"
            )
    else:
        # An empty explicit/environment/runtime session id is not equivalent to
        # a deliberately pending registration. Fail before creating a run row
        # so callers cannot accidentally create an unjoinable workflow.
        _session_registry_path(registry_root, raw_target_session_id)
    registry_path = (
        _pending_registry_path(registry_root, workflow_run_id)
        if pending
        else _session_registry_path(registry_root, raw_target_session_id)
    )
    state_session_id = (
        _pending_session_id(workflow_run_id)
        if pending
        else raw_target_session_id
    )
    scope = scope_contract or ScopeContract()
    registration = WorkflowRunRegistration(
        workflow_run_id=str(workflow_run_id),
        target_session_id="" if pending else raw_target_session_id,
        task_id=str(task_id),
        task=str(task),
        target_kind=str(target_kind),
        registry_path=registry_path,
        session_id_source=(
            PENDING_SESSION_SOURCE if pending else str(session_id_source)
        ),
    )
    config_snapshot = {
        "source": "workflow_submission",
        "schema_version": RUN_REGISTRATION_SCHEMA,
        "workflow_run_id": registration.workflow_run_id,
        "target_session_id": registration.target_session_id or None,
        "task_id": registration.task_id,
        "target_kind": registration.target_kind,
        "cwd": str(Path(cwd).expanduser().resolve()),
        "session_id_source": registration.session_id_source,
    }
    state.register_run(
        run_id=registration.workflow_run_id,
        session_id=state_session_id,
        rollout_path=(
            f"pending://{registration.target_kind}/{registration.workflow_run_id}"
            if pending
            else (
                f"pending://{registration.target_kind}/"
                f"{registration.target_session_id}"
            )
        ),
        task=registration.task,
        scope=scope,
        target_kind=registration.target_kind,
        config_snapshot=config_snapshot,
    )
    metadata = {
        **registration.event_payload(),
        "run_id": registration.workflow_run_id,
        "session_id": state_session_id,
        "scope_contract": scope.to_dict(),
        "config_snapshot": config_snapshot,
        "registered_at": int(time.time()),
    }
    _atomic_write_json(registration.registry_path, metadata)
    return registration


def reserve_launch_receipt(
    *,
    state: State,
    registry_dir: str | Path,
    workflow_run_id: str,
    task_id: str,
    target_kind: str,
    cwd: str | Path,
    ttl_s: int = DEFAULT_LAUNCH_RECEIPT_TTL_S,
    now: int | float | None = None,
) -> LaunchReceipt:
    """Reserve one cryptographic receipt before spawning a target runtime.

    The nonce is returned to the launcher and only its domain-separated SHA-256
    digest is persisted. Workflow, task, and target identity are explicit join
    fields; cwd is checked only as metadata for that already-selected workflow.
    """
    registry_root = Path(registry_dir).expanduser().resolve()
    registry_root.mkdir(parents=True, exist_ok=True)
    normalized_workflow_run_id = str(workflow_run_id).strip()
    normalized_task_id = str(task_id).strip()
    normalized_target_kind = str(target_kind).strip()
    normalized_cwd = _resolved_cwd(cwd)
    if not normalized_workflow_run_id:
        raise LaunchReceiptError("workflow_run_id is required")
    if not normalized_task_id:
        raise LaunchReceiptError("task_id is required")
    if not normalized_target_kind:
        raise LaunchReceiptError("target_kind is required")
    lifetime = int(ttl_s)
    if lifetime <= 0:
        raise LaunchReceiptError("launch receipt ttl_s must be positive")

    _validate_pending_launch_registration(
        state=state,
        registry_root=registry_root,
        workflow_run_id=normalized_workflow_run_id,
        task_id=normalized_task_id,
        target_kind=normalized_target_kind,
        cwd=normalized_cwd,
    )
    issued_at = _timestamp(now)
    expires_at = issued_at + lifetime
    for _attempt in range(16):
        launch_id = secrets.token_hex(16)
        nonce = secrets.token_urlsafe(32)
        pending_path, _ = _launch_receipt_paths(registry_root, launch_id)
        payload = {
            "schema_version": LAUNCH_RECEIPT_SCHEMA,
            "status": "pending",
            "launch_id": launch_id,
            "nonce_sha256": _launch_nonce_digest(launch_id, nonce),
            "workflow_run_id": normalized_workflow_run_id,
            "task_id": normalized_task_id,
            "target_kind": normalized_target_kind,
            "cwd": normalized_cwd,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
        try:
            _exclusive_write_json(pending_path, payload)
        except FileExistsError:
            continue
        return LaunchReceipt(
            launch_id=launch_id,
            nonce=nonce,
            workflow_run_id=normalized_workflow_run_id,
            task_id=normalized_task_id,
            target_kind=normalized_target_kind,
            cwd=normalized_cwd,
            issued_at=issued_at,
            expires_at=expires_at,
            receipt_path=pending_path,
        )
    raise RuntimeError("unable to reserve a unique launch receipt")


def consume_launch_receipt(
    *,
    state: State,
    registry_dir: str | Path,
    launch_id: str,
    nonce: str,
    workflow_run_id: str,
    task_id: str,
    target_kind: str,
    target_session_id: str,
    cwd: str | Path | None = None,
    rollout_path: str | None = None,
    now: int | float | None = None,
) -> dict[str, Any]:
    """Atomically consume one matching receipt and bind its real session once."""
    registry_root = Path(registry_dir).expanduser().resolve()
    registry_root.mkdir(parents=True, exist_ok=True)
    normalized_launch_id = _safe_launch_id(launch_id)
    normalized_nonce = str(nonce).strip()
    normalized_workflow_run_id = str(workflow_run_id).strip()
    normalized_task_id = str(task_id).strip()
    normalized_target_kind = str(target_kind).strip()
    normalized_target_session_id = str(target_session_id).strip()
    normalized_cwd = _resolved_cwd(cwd) if cwd is not None else None
    _session_registry_path(registry_root, normalized_target_session_id)

    pending_path, consumed_path = _launch_receipt_paths(
        registry_root,
        normalized_launch_id,
    )
    if consumed_path.exists():
        raise LaunchReceiptError(
            f"launch receipt already consumed: {normalized_launch_id}"
        )
    receipt = _read_registration_file(registry_root, pending_path)
    if receipt is None:
        if consumed_path.exists():
            raise LaunchReceiptError(
                f"launch receipt already consumed: {normalized_launch_id}"
            )
        raise LaunchReceiptError(
            f"launch receipt not found: {normalized_launch_id}"
        )
    _validate_launch_receipt_payload(
        receipt,
        launch_id=normalized_launch_id,
        nonce=normalized_nonce,
        workflow_run_id=normalized_workflow_run_id,
        task_id=normalized_task_id,
        target_kind=normalized_target_kind,
        cwd=normalized_cwd,
        now=_timestamp(now),
    )
    _validate_pending_launch_registration(
        state=state,
        registry_root=registry_root,
        workflow_run_id=normalized_workflow_run_id,
        task_id=normalized_task_id,
        target_kind=normalized_target_kind,
        cwd=str(receipt["cwd"]),
    )

    # Creating the consumed hard link is the single-winner operation. A second
    # process or thread cannot create the same destination, even while the
    # winner is still updating SQLite and the session sidecar.
    consumed_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(pending_path, consumed_path, follow_symlinks=False)
    except FileExistsError as exc:
        raise LaunchReceiptError(
            f"launch receipt already consumed: {normalized_launch_id}"
        ) from exc
    except FileNotFoundError as exc:
        if consumed_path.exists():
            raise LaunchReceiptError(
                f"launch receipt already consumed: {normalized_launch_id}"
            ) from exc
        raise LaunchReceiptError(
            f"launch receipt not found: {normalized_launch_id}"
        ) from exc
    try:
        pending_path.unlink()
    except FileNotFoundError:
        pass

    consumed_at = _timestamp(now)
    claimed = {
        **receipt,
        "status": "consuming",
        "consumed_at": consumed_at,
        "target_session_id": normalized_target_session_id,
    }
    _atomic_write_json(consumed_path, claimed)
    try:
        bound = bind_workflow_target_session(
            state=state,
            registry_dir=registry_root,
            workflow_run_id=normalized_workflow_run_id,
            target_session_id=normalized_target_session_id,
            source=LAUNCH_RECEIPT_SOURCE,
            rollout_path=rollout_path,
            launch_id=normalized_launch_id,
            launch_receipt_path=consumed_path,
        )
    except Exception as exc:
        _atomic_write_json(
            consumed_path,
            {
                **claimed,
                "status": "consume_failed",
                "failure_type": type(exc).__name__,
            },
        )
        raise
    _atomic_write_json(
        consumed_path,
        {
            **claimed,
            "status": "consumed",
            "session_registry_path": bound["registry_path"],
        },
    )
    return bound


def bind_workflow_target_session(
    *,
    state: State,
    registry_dir: str | Path,
    workflow_run_id: str,
    target_session_id: str,
    source: str = "runtime_receipt",
    rollout_path: str | None = None,
    launch_id: str | None = None,
    launch_receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Bind one pending workflow to a real target session without splitting runs."""
    registry_root = Path(registry_dir).expanduser().resolve()
    registry_root.mkdir(parents=True, exist_ok=True)
    normalized_target_session_id = str(target_session_id).strip()
    # Validate before reading or mutating any state.
    actual_path = _session_registry_path(
        registry_root,
        normalized_target_session_id,
    )
    pending_path = _pending_registry_path(registry_root, workflow_run_id)
    pending = _read_registration_file(registry_root, pending_path)
    if pending is None:
        existing = load_session_registration(
            registry_root,
            normalized_target_session_id,
        )
        if (
            existing is not None
            and existing.get("workflow_run_id") == str(workflow_run_id)
        ):
            return existing
        raise KeyError(f"pending workflow registration not found: {workflow_run_id}")

    sidecar_present = actual_path.is_file() or actual_path.is_symlink()
    if sidecar_present:
        existing = _read_registration_file(registry_root, actual_path)
        if existing is None:
            raise RuntimeError(
                "target session sidecar exists but is unreadable; "
                "treating it as bound"
            )
        if str(existing.get("workflow_run_id") or "") != str(workflow_run_id):
            raise RuntimeError(
                "target session sidecar is already bound to another workflow"
            )
    resolved_rollout_path = (
        str(rollout_path)
        if rollout_path
        else (
            f"pending://{pending.get('target_kind') or 'unknown'}/"
            f"{normalized_target_session_id}"
        )
    )
    current_run = state.get_run(str(workflow_run_id))
    expected_pending_session = _pending_session_id(str(workflow_run_id))
    if current_run is None:
        raise KeyError(f"run not found: {workflow_run_id}")
    current_session = str(current_run["session_id"] or "")
    if current_session not in {
        expected_pending_session,
        normalized_target_session_id,
    }:
        raise RuntimeError(
            "workflow run is not pending and is already bound to another session"
        )
    bound_at = int(time.time())
    metadata = {
        **pending,
        "target_session_id": normalized_target_session_id,
        "session_id": normalized_target_session_id,
        "join_key": normalized_target_session_id,
        "session_id_source": str(source),
        "registry_path": str(actual_path),
        "pending": False,
        "bound_at": bound_at,
    }
    if launch_id:
        metadata["launch_id"] = str(launch_id)
    if launch_receipt_path:
        metadata["launch_receipt_path"] = str(launch_receipt_path)
    claimed = False
    if not sidecar_present:
        try:
            _exclusive_write_json(actual_path, metadata)
        except FileExistsError as exc:
            racing = _read_registration_file(registry_root, actual_path)
            if (
                racing is None
                or str(racing.get("workflow_run_id") or "")
                != str(workflow_run_id)
            ):
                raise RuntimeError(
                    "target session sidecar is already bound to another "
                    "workflow"
                ) from exc
        else:
            claimed = True
    try:
        state.bind_run_session(
            run_id=str(workflow_run_id),
            session_id=normalized_target_session_id,
            rollout_path=resolved_rollout_path,
        )
    except Exception:
        if claimed:
            try:
                actual_path.unlink()
            except FileNotFoundError:
                pass
        raise
    if not claimed:
        _atomic_write_json(actual_path, metadata)
    try:
        pending_path.unlink()
    except FileNotFoundError:
        pass
    state.write_event(
        run_id=str(workflow_run_id),
        source="supervisor",
        kind="workflow_target_session_bound",
        payload={
            "workflow_run_id": str(workflow_run_id),
            "target_session_id": normalized_target_session_id,
            "session_id_source": str(source),
            "registry_path": str(actual_path),
            "task_id": str(pending.get("task_id") or ""),
            "target_kind": str(pending.get("target_kind") or ""),
            **({"launch_id": str(launch_id)} if launch_id else {}),
        },
    )
    return metadata


def bind_unambiguous_pending_workflow(
    *,
    state: State,
    registry_dir: str | Path,
    target_session_id: str,
    rollout_path: str,
    cwd: str | Path | None = None,
) -> dict[str, Any] | None:
    """Reject legacy first-rollout inference.

    Cwd and "only pending workflow" are not authority. Kept as a compatibility
    shim for callers that have not yet removed the old probe; it never binds.
    """
    registry_root = Path(registry_dir).expanduser().resolve()
    normalized_target_session_id = str(target_session_id).strip()
    _session_registry_path(registry_root, normalized_target_session_id)
    _ = (state, rollout_path, cwd)
    return None


def load_session_registration(
    registry_dir: str | Path,
    session_id: str,
) -> dict[str, Any] | None:
    """Load a valid session sidecar, returning None for absent/bad metadata."""
    registry_root = Path(registry_dir).expanduser().resolve()
    path = _session_registry_path(registry_root, session_id)
    payload = _read_registration_file(registry_root, path)
    if payload is None:
        return None
    registered_session = str(
        payload.get("target_session_id")
        or payload.get("session_id")
        or ""
    ).strip()
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or ""
    ).strip()
    if registered_session and registered_session != str(session_id):
        return None
    if not workflow_run_id:
        return None
    payload["workflow_run_id"] = workflow_run_id
    payload["target_session_id"] = registered_session or str(session_id)
    payload["registry_path"] = str(path)
    return payload


def _validate_pending_launch_registration(
    *,
    state: State,
    registry_root: Path,
    workflow_run_id: str,
    task_id: str,
    target_kind: str,
    cwd: str,
) -> dict[str, Any]:
    pending_path = _pending_registry_path(registry_root, workflow_run_id)
    pending = _read_registration_file(registry_root, pending_path)
    if pending is None or pending.get("pending") is not True:
        raise LaunchReceiptError(
            f"pending workflow registration not found: {workflow_run_id}"
        )
    for field, expected in (
        ("workflow_run_id", workflow_run_id),
        ("task_id", task_id),
        ("target_kind", target_kind),
    ):
        observed = str(pending.get(field) or "").strip()
        if observed != expected:
            raise LaunchReceiptError(
                f"pending workflow registration {field} mismatch"
            )
    config_snapshot = pending.get("config_snapshot")
    registered_cwd = str(
        config_snapshot.get("cwd")
        if isinstance(config_snapshot, dict)
        else ""
    ).strip()
    if not registered_cwd or _resolved_cwd(registered_cwd) != cwd:
        raise LaunchReceiptError("pending workflow registration cwd mismatch")
    run = state.get_run(workflow_run_id)
    if run is None:
        raise LaunchReceiptError(f"run not found: {workflow_run_id}")
    if (
        str(run["status"] or "") != "running"
        or str(run["session_id"] or "") != _pending_session_id(workflow_run_id)
    ):
        raise LaunchReceiptError(
            "workflow run is not pending for launch receipt binding"
        )
    return pending


def _validate_launch_receipt_payload(
    receipt: Mapping[str, Any],
    *,
    launch_id: str,
    nonce: str,
    workflow_run_id: str,
    task_id: str,
    target_kind: str,
    cwd: str | None,
    now: int,
) -> None:
    if receipt.get("schema_version") != LAUNCH_RECEIPT_SCHEMA:
        raise LaunchReceiptError("launch receipt schema mismatch")
    if receipt.get("status") != "pending":
        raise LaunchReceiptError(f"launch receipt already consumed: {launch_id}")
    for field, expected in (
        ("launch_id", launch_id),
        ("workflow_run_id", workflow_run_id),
        ("task_id", task_id),
        ("target_kind", target_kind),
    ):
        observed = str(receipt.get(field) or "").strip()
        if observed != expected:
            raise LaunchReceiptError(f"launch receipt {field} mismatch")
    observed_nonce_hash = str(receipt.get("nonce_sha256") or "").strip()
    expected_nonce_hash = _launch_nonce_digest(launch_id, nonce)
    if not observed_nonce_hash or not hmac.compare_digest(
        observed_nonce_hash,
        expected_nonce_hash,
    ):
        raise LaunchReceiptError("launch receipt nonce mismatch")
    try:
        issued_at = int(receipt["issued_at"])
        expires_at = int(receipt["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LaunchReceiptError("launch receipt timestamp is invalid") from exc
    if now < issued_at:
        raise LaunchReceiptError("launch receipt is not yet valid")
    if now >= expires_at:
        raise LaunchReceiptError("launch receipt expired")
    registered_cwd = str(receipt.get("cwd") or "").strip()
    if not registered_cwd:
        raise LaunchReceiptError("launch receipt cwd is missing")
    if cwd is not None and _resolved_cwd(registered_cwd) != cwd:
        raise LaunchReceiptError("launch receipt cwd mismatch")


def _read_registration_file(
    registry_root: Path,
    path: Path,
) -> dict[str, Any] | None:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(registry_root)
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _session_registry_path(registry_root: Path, session_id: str) -> Path:
    raw = str(session_id).strip()
    if (
        not raw
        or raw in {".", ".."}
        or "\x00" in raw
        or "/" in raw
        or "\\" in raw
        or Path(raw).name != raw
    ):
        raise ValueError("target session id is not a safe registry filename")
    candidate = (registry_root / f"{raw}.json").resolve(strict=False)
    if candidate.parent != registry_root:
        raise ValueError("target session registry path escapes registry root")
    return candidate


def _safe_launch_id(launch_id: str) -> str:
    raw = str(launch_id).strip()
    if (
        not raw
        or raw in {".", ".."}
        or "\x00" in raw
        or "/" in raw
        or "\\" in raw
        or Path(raw).name != raw
    ):
        raise LaunchReceiptError("launch_id is not a safe receipt identifier")
    return raw


def _launch_receipt_paths(
    registry_root: Path,
    launch_id: str,
) -> tuple[Path, Path]:
    normalized_launch_id = _safe_launch_id(launch_id)
    digest = hashlib.sha256(normalized_launch_id.encode("utf-8")).hexdigest()
    receipt_root = registry_root / _LAUNCH_RECEIPT_DIRNAME
    return (
        receipt_root / "pending" / f"{digest}.json",
        receipt_root / "consumed" / f"{digest}.json",
    )


def _pending_registry_path(
    registry_root: Path,
    workflow_run_id: str,
) -> Path:
    workflow = str(workflow_run_id).strip()
    if not workflow:
        raise ValueError("workflow_run_id is required")
    digest = hashlib.sha256(workflow.encode("utf-8")).hexdigest()[:32]
    return registry_root / f".pending-{digest}.json"


def _pending_session_id(workflow_run_id: str) -> str:
    return f"pending:{workflow_run_id}"


def _resolved_cwd(cwd: str | Path) -> str:
    raw = str(cwd).strip()
    if not raw:
        raise LaunchReceiptError("cwd is required")
    return str(Path(raw).expanduser().resolve())


def _timestamp(now: int | float | None) -> int:
    return int(time.time() if now is None else now)


def _launch_nonce_digest(launch_id: str, nonce: str) -> str:
    material = (
        f"{LAUNCH_RECEIPT_SCHEMA}\x00{launch_id}\x00{str(nonce)}"
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _exclusive_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
