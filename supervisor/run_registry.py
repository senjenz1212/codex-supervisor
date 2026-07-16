"""Workflow-to-target run registration and sidecar joins.

The workflow run id is the supervisor's durable identity. The target session
id is the rollout filename identity. A session-keyed sidecar joins the two
before target events arrive, and State.register_run co-registers the workflow
run so active-run monitoring and drift use the same id as the workflow ledger.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any, Iterator, Mapping
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - launch receipts are POSIX-only
    fcntl = None  # type: ignore[assignment]

from .state import State
from .target.types import ScopeContract


RUN_REGISTRATION_SCHEMA_V2 = "supervisor-run-registration/v2"
RUN_REGISTRATION_SCHEMA = "supervisor-run-registration/v3"
_SUPPORTED_RUN_REGISTRATION_SCHEMAS = frozenset(
    {RUN_REGISTRATION_SCHEMA_V2, RUN_REGISTRATION_SCHEMA}
)
LAUNCH_RECEIPT_SCHEMA = "supervisor-launch-receipt/v1"
PENDING_SESSION_SOURCE = "pending_runtime_receipt"
LAUNCH_RECEIPT_SOURCE = "launch_receipt"
REUSABLE_SESSION_COMPLETION_POLICY = "reusable_session"
SINGLE_TURN_COMPLETION_POLICY = "single_turn"
WORKFLOW_AGGREGATE_COMPLETION_POLICY = "workflow_aggregate"
_COMPLETION_POLICIES = frozenset({
    REUSABLE_SESSION_COMPLETION_POLICY,
    SINGLE_TURN_COMPLETION_POLICY,
    WORKFLOW_AGGREGATE_COMPLETION_POLICY,
})
DEFAULT_LAUNCH_RECEIPT_TTL_S = 3_600
_LAUNCH_RECEIPT_DIRNAME = ".launch-receipts"


def _supported_registration_schema(value: Any, *, label: str) -> str:
    schema = str(value or "").strip()
    if schema not in _SUPPORTED_RUN_REGISTRATION_SCHEMAS:
        raise RuntimeError(f"{label} schema_version mismatch")
    return schema


def _runtime_target_run_id(
    *,
    schema_version: str,
    workflow_run_id: str,
    target_session_id: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{schema_version}\0target-run\0"
            f"{workflow_run_id}\0{target_session_id}"
        ).encode("utf-8")
    ).hexdigest()
    return f"target-{digest[:32]}"


def _validate_runtime_task_identity(
    *,
    schema_version: str,
    task: str,
    config_snapshot: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
    label: str,
) -> str:
    expected = hashlib.sha256(str(task).encode("utf-8")).hexdigest()
    observed_config = str(
        config_snapshot.get("task_sha256") or ""
    ).strip()
    observed_payload = (
        ""
        if payload is None
        else str(payload.get("task_sha256") or "").strip()
    )
    if schema_version == RUN_REGISTRATION_SCHEMA and (
        not observed_config or (payload is not None and not observed_payload)
    ):
        raise RuntimeError(f"{label} task_sha256 is missing")
    for surface, observed in (
        ("config_snapshot", observed_config),
        ("sidecar", observed_payload),
    ):
        if not observed:
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", observed) or observed != expected:
            raise RuntimeError(f"{label} {surface} task_sha256 mismatch")
    return expected


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
    completion_policy: str

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
            "completion_policy": self.completion_policy,
            "registry_path": str(self.registry_path),
            "pending": pending,
        }
        if job_id:
            payload["job_id"] = job_id
        return payload


def validate_run_registration_authority(
    *,
    state: State,
    run_id: str,
    expected_workflow_run_id: str | None = None,
    expected_task_id: str | None = None,
    expected_target_kind: str | None = None,
    expected_cwd: str | Path | None = None,
    expected_completion_policy: str | None = None,
    require_workflow_registration: bool = False,
) -> dict[str, Any]:
    """Validate one registration for evidence publication or reuse."""
    return _validate_run_registration_authority(
        state=state,
        run_id=run_id,
        expected_workflow_run_id=expected_workflow_run_id,
        expected_task_id=expected_task_id,
        expected_target_kind=expected_target_kind,
        expected_cwd=expected_cwd,
        expected_completion_policy=expected_completion_policy,
        require_workflow_registration=require_workflow_registration,
        require_pending_workflow=False,
    )


def validate_pending_workflow_registration_authority(
    *,
    state: State,
    run_id: str,
    expected_workflow_run_id: str,
    expected_task_id: str,
    expected_target_kind: str,
    expected_cwd: str | Path,
    expected_completion_policy: str | None = None,
) -> dict[str, Any]:
    """Validate immutable workflow authority while it is still pre-launch."""
    return _validate_run_registration_authority(
        state=state,
        run_id=run_id,
        expected_workflow_run_id=expected_workflow_run_id,
        expected_task_id=expected_task_id,
        expected_target_kind=expected_target_kind,
        expected_cwd=expected_cwd,
        expected_completion_policy=expected_completion_policy,
        require_workflow_registration=True,
        require_pending_workflow=True,
    )


def _validate_run_registration_authority(
    *,
    state: State,
    run_id: str,
    expected_workflow_run_id: str | None,
    expected_task_id: str | None,
    expected_target_kind: str | None,
    expected_cwd: str | Path | None,
    expected_completion_policy: str | None,
    require_workflow_registration: bool,
    require_pending_workflow: bool,
) -> dict[str, Any]:
    """Validate that one persisted run is complete enough to grant authority.

    Legacy rows remain readable, but mere row presence never authorizes target
    execution or evidence publication. Workflow callers additionally bind the
    immutable submission fields they are about to rely on.
    """
    normalized_run_id = str(run_id).strip()
    if not normalized_run_id:
        raise RuntimeError("run registration run_id is missing")
    run = state.get_run(normalized_run_id)
    snapshot = state.get_run_snapshot(normalized_run_id)
    label = (
        "workflow run registration"
        if require_workflow_registration
        else "run registration"
    )
    if run is None or snapshot is None:
        raise RuntimeError(f"{label} is incomplete: {normalized_run_id}")
    run_payload = dict(run)
    snapshot_payload = dict(snapshot)
    if str(run_payload.get("run_id") or "").strip() != normalized_run_id:
        raise RuntimeError(f"{label} run_id mismatch")
    if str(snapshot_payload.get("run_id") or "").strip() != normalized_run_id:
        raise RuntimeError(f"{label} snapshot run_id mismatch")
    for field in ("session_id", "rollout_path", "task"):
        if not str(run_payload.get(field) or "").strip():
            raise RuntimeError(f"{label} {field} is missing")
    snapshot_target_kind = str(
        snapshot_payload.get("target_kind") or ""
    ).strip()
    if not snapshot_target_kind:
        raise RuntimeError(f"{label} target_kind is missing")
    try:
        config_snapshot = json.loads(
            str(snapshot_payload.get("config_json") or "")
        )
        scope_snapshot = json.loads(
            str(snapshot_payload.get("scope_contract_json") or "")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} snapshot is not valid JSON") from exc
    if not isinstance(config_snapshot, dict) or not config_snapshot:
        raise RuntimeError(f"{label} config snapshot is missing")
    if not isinstance(scope_snapshot, dict):
        raise RuntimeError(f"{label} scope snapshot is not an object")
    try:
        ScopeContract.from_dict(scope_snapshot)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} scope snapshot is invalid") from exc

    workflow_source = str(config_snapshot.get("source") or "").strip()
    is_workflow_registration = workflow_source in {
        "workflow_submission",
        "workflow_runtime_session",
    }
    if (
        require_workflow_registration
        and workflow_source != "workflow_submission"
    ):
        raise RuntimeError(f"{label} source mismatch")
    if is_workflow_registration:
        registration_schema = _supported_registration_schema(
            config_snapshot.get("schema_version"),
            label=label,
        )
        required_config_fields = (
            "workflow_run_id",
            "task_id",
            "target_kind",
            "cwd",
            "session_id_source",
            "completion_policy",
        )
        for field in required_config_fields:
            if not str(config_snapshot.get(field) or "").strip():
                raise RuntimeError(f"{label} {field} is missing")
        if (
            str(config_snapshot["target_kind"]).strip()
            != snapshot_target_kind
        ):
            raise RuntimeError(f"{label} target_kind mismatch")
        completion_policy = str(
            config_snapshot["completion_policy"]
        ).strip()
        if completion_policy not in _COMPLETION_POLICIES:
            raise RuntimeError(f"{label} completion_policy is invalid")
        _resolved_cwd(config_snapshot["cwd"])
        if workflow_source == "workflow_submission":
            registered_workflow_run_id = str(
                config_snapshot["workflow_run_id"]
            ).strip()
            if registered_workflow_run_id != normalized_run_id:
                raise RuntimeError(f"{label} workflow_run_id mismatch")
            session_id_source = str(
                config_snapshot["session_id_source"]
            ).strip()
            if session_id_source == PENDING_SESSION_SOURCE:
                if str(
                    config_snapshot.get("target_session_id") or ""
                ).strip():
                    raise RuntimeError(
                        f"{label} pending target_session_id mismatch"
                    )
                observed_session_id = str(run_payload["session_id"]).strip()
                pending_session_id = _pending_session_id(normalized_run_id)
                if observed_session_id == pending_session_id:
                    expected_rollout = (
                        f"pending://{snapshot_target_kind}/{normalized_run_id}"
                    )
                    if (
                        str(run_payload["rollout_path"]).strip()
                        != expected_rollout
                    ):
                        raise RuntimeError(
                            f"{label} pending rollout_path mismatch"
                        )
                elif require_pending_workflow:
                    raise RuntimeError(f"{label} pending session_id mismatch")
                else:
                    _validate_workflow_target_session_binding_event(
                        state=state,
                        workflow_run_id=normalized_run_id,
                        target_session_id=observed_session_id,
                        expected={
                            "workflow_run_id": normalized_run_id,
                            "target_session_id": observed_session_id,
                            "task_id": str(
                                config_snapshot["task_id"]
                            ).strip(),
                            "target_kind": snapshot_target_kind,
                        },
                        label=label,
                    )
            else:
                if require_pending_workflow:
                    raise RuntimeError(f"{label} pending source mismatch")
                target_session_id = str(
                    config_snapshot.get("target_session_id") or ""
                ).strip()
                if not target_session_id:
                    raise RuntimeError(f"{label} target_session_id is missing")
                if str(run_payload["session_id"]).strip() != target_session_id:
                    raise RuntimeError(f"{label} target_session_id mismatch")
        else:
            _validate_workflow_runtime_session_authority(
                state=state,
                run_id=normalized_run_id,
                run_payload=run_payload,
                config_snapshot=config_snapshot,
                schema_version=registration_schema,
                label=label,
            )

    expected_values = (
        (
            "workflow_run_id",
            expected_workflow_run_id,
            str(config_snapshot.get("workflow_run_id") or "").strip(),
        ),
        (
            "task_id",
            expected_task_id,
            str(config_snapshot.get("task_id") or "").strip(),
        ),
        (
            "target_kind",
            expected_target_kind,
            str(config_snapshot.get("target_kind") or "").strip(),
        ),
        (
            "completion_policy",
            expected_completion_policy,
            str(config_snapshot.get("completion_policy") or "").strip(),
        ),
    )
    for field, expected, observed in expected_values:
        if expected is not None and observed != str(expected).strip():
            raise RuntimeError(f"{label} {field} mismatch")
    if expected_cwd is not None:
        observed_cwd = str(config_snapshot.get("cwd") or "").strip()
        if (
            not observed_cwd
            or _resolved_cwd(observed_cwd) != _resolved_cwd(expected_cwd)
        ):
            raise RuntimeError(f"{label} cwd mismatch")
    return {
        "run": run_payload,
        "snapshot": snapshot_payload,
        "config_snapshot": config_snapshot,
        "scope_contract": scope_snapshot,
    }


def _validate_workflow_runtime_session_authority(
    *,
    state: State,
    run_id: str,
    run_payload: Mapping[str, Any],
    config_snapshot: Mapping[str, Any],
    schema_version: str,
    label: str,
) -> None:
    for field in (
        "target_run_id",
        "target_session_id",
        "gate",
        "runtime_run_id",
        "runtime_result_hash",
    ):
        if not str(config_snapshot.get(field) or "").strip():
            raise RuntimeError(f"{label} {field} is missing")
    workflow_run_id = str(
        config_snapshot["workflow_run_id"]
    ).strip()
    target_run_id = str(config_snapshot["target_run_id"]).strip()
    target_session_id = str(
        config_snapshot["target_session_id"]
    ).strip()
    task_id = str(config_snapshot["task_id"]).strip()
    target_kind = str(config_snapshot["target_kind"]).strip()
    cwd = str(config_snapshot["cwd"]).strip()
    gate = str(config_snapshot["gate"]).strip()
    runtime_run_id = str(config_snapshot["runtime_run_id"]).strip()
    runtime_result_hash = str(
        config_snapshot["runtime_result_hash"]
    ).strip()
    task_sha256 = _validate_runtime_task_identity(
        schema_version=schema_version,
        task=str(run_payload.get("task") or ""),
        config_snapshot=config_snapshot,
        label=label,
    )
    session_id_source = str(
        config_snapshot["session_id_source"]
    ).strip()
    completion_policy = str(
        config_snapshot["completion_policy"]
    ).strip()
    if target_run_id != run_id:
        raise RuntimeError(f"{label} target_run_id mismatch")
    if str(run_payload["session_id"]).strip() != target_session_id:
        raise RuntimeError(f"{label} target_session_id mismatch")
    if workflow_run_id == run_id:
        raise RuntimeError(f"{label} workflow_run_id mismatch")
    expected_target_run_id = _runtime_target_run_id(
        schema_version=schema_version,
        workflow_run_id=workflow_run_id,
        target_session_id=target_session_id,
    )
    if target_run_id != expected_target_run_id:
        raise RuntimeError(f"{label} target_run_id mismatch")
    if session_id_source == PENDING_SESSION_SOURCE:
        raise RuntimeError(f"{label} session_id_source mismatch")
    if completion_policy != SINGLE_TURN_COMPLETION_POLICY:
        raise RuntimeError(f"{label} completion_policy mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", runtime_result_hash):
        raise RuntimeError(f"{label} runtime_result_hash is invalid")
    parent_authority = _validate_run_registration_authority(
        state=state,
        run_id=workflow_run_id,
        expected_workflow_run_id=workflow_run_id,
        expected_task_id=task_id,
        expected_target_kind=None,
        expected_cwd=cwd,
        expected_completion_policy=None,
        require_workflow_registration=True,
        require_pending_workflow=False,
    )
    if str(parent_authority["run"].get("task") or "") != str(
        run_payload.get("task") or ""
    ):
        raise RuntimeError(f"{label} parent task mismatch")

    expected_binding = {
        "schema_version": schema_version,
        "workflow_run_id": workflow_run_id,
        "target_run_id": target_run_id,
        "target_session_id": target_session_id,
        "task_id": task_id,
        "target_kind": target_kind,
        "gate": gate,
        "runtime_run_id": runtime_run_id,
        "runtime_result_hash": runtime_result_hash,
        "session_id_source": session_id_source,
        "completion_policy": completion_policy,
    }
    if (
        schema_version == RUN_REGISTRATION_SCHEMA
        or str(config_snapshot.get("task_sha256") or "").strip()
    ):
        expected_binding["task_sha256"] = task_sha256
    _validate_workflow_target_session_binding_event(
        state=state,
        workflow_run_id=workflow_run_id,
        target_session_id=target_session_id,
        expected=expected_binding,
        label=label,
    )


def _workflow_target_session_binding_idempotency_key(
    *,
    workflow_run_id: str,
    target_session_id: str,
) -> str:
    digest = hashlib.sha256(
        "\x1f".join((workflow_run_id, target_session_id)).encode("utf-8")
    ).hexdigest()
    return f"workflow-target-session-bound:{digest}"


def _validate_workflow_target_session_binding_event(
    *,
    state: State,
    workflow_run_id: str,
    target_session_id: str,
    expected: Mapping[str, str],
    label: str,
) -> None:
    exact_match = False
    after_event_id = 0
    while True:
        events = state.read_events_since(
            workflow_run_id,
            after_event_id=after_event_id,
            limit=1_000,
        )
        if not events:
            break
        for event in events:
            if (
                event.get("source") != "supervisor"
                or event.get("kind") != "workflow_target_session_bound"
            ):
                continue
            payload = event.get("payload")
            if (
                not isinstance(payload, Mapping)
                or str(payload.get("target_session_id") or "").strip()
                != target_session_id
            ):
                continue
            discrepancies = [
                field
                for field, expected_value in expected.items()
                if str(payload.get(field) or "").strip() != expected_value
            ]
            session_id_source = str(
                payload.get("session_id_source") or ""
            ).strip()
            runtime_run_id = str(
                payload.get("runtime_run_id") or ""
            ).strip()
            runtime_result_hash = str(
                payload.get("runtime_result_hash") or ""
            ).strip()
            if (
                not session_id_source
                or session_id_source == PENDING_SESSION_SOURCE
            ):
                discrepancies.append("session_id_source")
            if not str(payload.get("registry_path") or "").strip():
                discrepancies.append("registry_path")
            if bool(runtime_run_id) != bool(runtime_result_hash):
                discrepancies.append("runtime_receipt")
            elif runtime_result_hash and not re.fullmatch(
                r"[0-9a-f]{64}",
                runtime_result_hash,
            ):
                discrepancies.append("runtime_result_hash")
            if discrepancies:
                raise RuntimeError(
                    f"{label} binding event mismatch: "
                    + ", ".join(sorted(set(discrepancies)))
                )
            exact_match = True
        after_event_id = max(int(event["event_id"]) for event in events)
    if not exact_match:
        raise RuntimeError(f"{label} binding event is missing")


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


@dataclass(frozen=True)
class _LaunchReceiptStore:
    registry_root: Path
    registry_root_fd: int
    receipt_root_fd: int
    pending_fd: int
    consumed_fd: int
    locks_fd: int

    def path(self, bucket: str, name: str) -> Path:
        return (
            self.registry_root
            / _LAUNCH_RECEIPT_DIRNAME
            / bucket
            / name
        )

    def directory_fd(self, bucket: str) -> int:
        if bucket == "pending":
            return self.pending_fd
        if bucket == "consumed":
            return self.consumed_fd
        if bucket == "locks":
            return self.locks_fd
        raise ValueError(f"unknown launch receipt bucket: {bucket}")

    def exists(self, bucket: str, name: str) -> bool:
        try:
            os.stat(
                name,
                dir_fd=self.directory_fd(bucket),
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return True

    def read_json(
        self,
        bucket: str,
        name: str,
    ) -> dict[str, Any] | None:
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.directory_fd(bucket),
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                payload = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return payload if isinstance(payload, dict) else None

    def exclusive_write_json(
        self,
        bucket: str,
        name: str,
        payload: Mapping[str, Any],
    ) -> None:
        _exclusive_write_json_at(
            self.directory_fd(bucket),
            name,
            payload,
        )

    def atomic_exclusive_write_json(
        self,
        bucket: str,
        name: str,
        payload: Mapping[str, Any],
    ) -> None:
        _atomic_exclusive_write_json_at(
            self.directory_fd(bucket),
            name,
            payload,
        )

    def atomic_write_json(
        self,
        bucket: str,
        name: str,
        payload: Mapping[str, Any],
    ) -> None:
        _atomic_write_json_at(
            self.directory_fd(bucket),
            name,
            payload,
        )

    def unlink(self, bucket: str, name: str) -> bool:
        return _durable_unlink_at(
            self.directory_fd(bucket),
            name,
        )

    def assert_namespace_current(self) -> None:
        checks = (
            (
                self.registry_root_fd,
                _LAUNCH_RECEIPT_DIRNAME,
                self.receipt_root_fd,
            ),
            (self.receipt_root_fd, "pending", self.pending_fd),
            (self.receipt_root_fd, "consumed", self.consumed_fd),
            (self.receipt_root_fd, "locks", self.locks_fd),
        )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for parent_fd, name, expected_fd in checks:
            try:
                observed_fd = os.open(
                    name,
                    flags,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                raise LaunchReceiptError(
                    "launch receipt namespace changed during operation"
                ) from exc
            try:
                observed = os.fstat(observed_fd)
                expected = os.fstat(expected_fd)
                if (
                    observed.st_dev != expected.st_dev
                    or observed.st_ino != expected.st_ino
                ):
                    raise LaunchReceiptError(
                        "launch receipt namespace changed during operation"
                    )
            finally:
                os.close(observed_fd)


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
    completion_policy: str = REUSABLE_SESSION_COMPLETION_POLICY,
) -> WorkflowRunRegistration:
    """Co-register a workflow run and atomically write its session join."""
    registry_root = Path(registry_dir).expanduser().resolve()
    _ensure_directory_durable(registry_root)
    raw_target_session_id = str(target_session_id).strip()
    normalized_completion_policy = str(completion_policy).strip()
    if normalized_completion_policy not in _COMPLETION_POLICIES:
        raise ValueError(
            "completion_policy must be one of "
            f"{sorted(_COMPLETION_POLICIES)}"
        )
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
        completion_policy=normalized_completion_policy,
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
        "completion_policy": registration.completion_policy,
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
    try:
        _exclusive_write_json(registration.registry_path, metadata)
    except FileExistsError:
        existing = _read_registration_file(
            registry_root,
            registration.registry_path,
        )
        if existing is None:
            raise RuntimeError(
                "workflow session sidecar exists but is unreadable"
            ) from None
        _validate_submitted_workflow_registration(
            existing,
            expected=metadata,
            # A pending resubmission may legitimately carry a new intent for
            # the same provenance; the sidecar is replaced with it below.
            volatile_fields=(
                ("registered_at", "task")
                if pending
                else ("registered_at",)
            ),
        )
        if pending:
            if str(existing.get("task") or "") != registration.task:
                state.update_pending_run_task(
                    run_id=registration.workflow_run_id,
                    task=registration.task,
                )
            _replace_write_json(registration.registry_path, metadata)
    return registration


def _validate_submitted_workflow_registration(
    observed: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    volatile_fields: tuple[str, ...] = ("registered_at",),
) -> None:
    """Reject a resubmission that rebinds the sidecar to different provenance."""
    observed_schema = _supported_registration_schema(
        observed.get("schema_version"),
        label="target session sidecar",
    )
    expected_for_schema = dict(expected)
    expected_for_schema["schema_version"] = observed_schema
    expected_config = expected.get("config_snapshot")
    if isinstance(expected_config, Mapping):
        expected_for_schema["config_snapshot"] = {
            **expected_config,
            "schema_version": observed_schema,
        }
    discrepancies = [
        field
        for field in expected_for_schema
        if field not in volatile_fields
        and observed.get(field) != expected_for_schema.get(field)
    ]
    if discrepancies:
        raise RuntimeError(
            "target session sidecar provenance discrepancy: "
            + ", ".join(sorted(discrepancies))
        )


def register_workflow_runtime_session(
    *,
    state: State,
    registry_dir: str | Path,
    workflow_run_id: str,
    target_session_id: str,
    task_id: str,
    task: str,
    target_kind: str,
    cwd: str | Path,
    gate: str,
    runtime_run_id: str,
    runtime_result_hash: str,
    source: str = "runtime_result",
) -> dict[str, Any]:
    """Register one workflow-owned, one-shot target runtime session.

    A detached workflow may launch multiple target sessions across gates and
    retries. Each target session therefore receives its own child run while
    retaining an immutable join to the parent workflow run. The child is the
    unit terminalized by ``turn.completed``; the parent remains active until
    the detached workflow itself publishes its terminal outcome.
    """
    registry_root = Path(registry_dir).expanduser().resolve()
    _ensure_directory_durable(registry_root)
    normalized_workflow_run_id = str(workflow_run_id).strip()
    normalized_target_session_id = str(target_session_id).strip()
    normalized_task_id = str(task_id).strip()
    normalized_task = str(task).strip()
    normalized_target_kind = str(target_kind).strip()
    normalized_gate = str(gate).strip()
    normalized_runtime_run_id = str(runtime_run_id).strip()
    normalized_runtime_result_hash = str(runtime_result_hash).strip()
    normalized_source = str(source).strip()
    if not normalized_workflow_run_id:
        raise ValueError("workflow_run_id is required")
    if not normalized_target_session_id:
        raise ValueError("target_session_id is required")
    if not normalized_task_id:
        raise ValueError("task_id is required")
    if not normalized_task:
        raise ValueError("task is required")
    if not normalized_target_kind:
        raise ValueError("target_kind is required")
    if not normalized_gate:
        raise ValueError("gate is required")
    if not normalized_runtime_run_id:
        raise ValueError("runtime_run_id is required")
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_runtime_result_hash):
        raise ValueError("runtime_result_hash must be a canonical sha256")
    if not normalized_source:
        raise ValueError("source is required")
    normalized_cwd = _resolved_cwd(cwd)

    parent_run = state.get_run(normalized_workflow_run_id)
    parent_snapshot = state.get_run_snapshot(normalized_workflow_run_id)
    if parent_run is None or parent_snapshot is None:
        raise KeyError(
            f"workflow run is not registered: {normalized_workflow_run_id}"
        )
    parent_task = str(parent_run["task"] or "").strip()
    if not parent_task:
        raise RuntimeError("workflow run registration task is missing")
    if normalized_task != parent_task:
        raise ValueError(
            "runtime task does not match workflow run registration task"
        )
    try:
        parent_scope_payload = json.loads(
            str(parent_snapshot["scope_contract_json"] or "{}")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "workflow run scope snapshot is not valid JSON"
        ) from exc
    if not isinstance(parent_scope_payload, dict):
        raise RuntimeError("workflow run scope snapshot is not an object")
    scope = ScopeContract.from_dict(parent_scope_payload)
    try:
        parent_config_payload = json.loads(
            str(parent_snapshot["config_json"] or "{}")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "workflow run config snapshot is not valid JSON"
        ) from exc
    if not isinstance(parent_config_payload, dict):
        raise RuntimeError("workflow run config snapshot is not an object")
    registered_cwd = str(parent_config_payload.get("cwd") or "").strip()
    if not registered_cwd:
        raise RuntimeError("workflow run registration cwd is missing")
    if _resolved_cwd(registered_cwd) != normalized_cwd:
        raise ValueError(
            "runtime cwd does not match workflow run registration cwd"
        )
    validate_run_registration_authority(
        state=state,
        run_id=normalized_workflow_run_id,
        expected_workflow_run_id=normalized_workflow_run_id,
        expected_task_id=normalized_task_id,
        expected_cwd=normalized_cwd,
        require_workflow_registration=True,
    )
    task_sha256 = hashlib.sha256(
        normalized_task.encode("utf-8")
    ).hexdigest()

    session_path = _session_registry_path(
        registry_root,
        normalized_target_session_id,
    )
    target_run_id = _runtime_target_run_id(
        schema_version=RUN_REGISTRATION_SCHEMA,
        workflow_run_id=normalized_workflow_run_id,
        target_session_id=normalized_target_session_id,
    )
    config_snapshot = {
        "source": "workflow_runtime_session",
        "schema_version": RUN_REGISTRATION_SCHEMA,
        "workflow_run_id": normalized_workflow_run_id,
        "target_run_id": target_run_id,
        "target_session_id": normalized_target_session_id,
        "task_id": normalized_task_id,
        "target_kind": normalized_target_kind,
        "cwd": normalized_cwd,
        "gate": normalized_gate,
        "runtime_run_id": normalized_runtime_run_id,
        "runtime_result_hash": normalized_runtime_result_hash,
        "task_sha256": task_sha256,
        "session_id_source": normalized_source,
        "completion_policy": SINGLE_TURN_COMPLETION_POLICY,
    }
    metadata = {
        "schema_version": RUN_REGISTRATION_SCHEMA,
        "workflow_run_id": normalized_workflow_run_id,
        "run_id": target_run_id,
        "target_run_id": target_run_id,
        "target_session_id": normalized_target_session_id,
        "session_id": normalized_target_session_id,
        "task_id": normalized_task_id,
        "task": normalized_task,
        "target_kind": normalized_target_kind,
        "join_key": normalized_target_session_id,
        "session_id_source": normalized_source,
        "completion_policy": SINGLE_TURN_COMPLETION_POLICY,
        "registry_path": str(session_path),
        "pending": False,
        "scope_contract": scope.to_dict(),
        "config_snapshot": config_snapshot,
        "gate": normalized_gate,
        "runtime_run_id": normalized_runtime_run_id,
        "runtime_result_hash": normalized_runtime_result_hash,
        "task_sha256": task_sha256,
        "registered_at": int(time.time()),
    }
    existing = load_session_registration(
        registry_root,
        normalized_target_session_id,
    )
    claimed_sidecar = False
    registration_metadata = existing
    if registration_metadata is None:
        try:
            _exclusive_write_json(session_path, metadata)
            claimed_sidecar = True
            registration_metadata = metadata
        except FileExistsError:
            registration_metadata = load_session_registration(
                registry_root,
                normalized_target_session_id,
            )
            if registration_metadata is None:
                raise RuntimeError(
                    "target session sidecar exists but is unreadable"
                )
    try:
        _validate_runtime_session_registration(
            registration_metadata,
            expected=metadata,
        )
        _ensure_runtime_session_state_binding(
            state=state,
            metadata=registration_metadata,
            scope=scope,
        )
    except Exception:
        if claimed_sidecar:
            _durable_unlink(session_path)
        raise
    return registration_metadata


def _validate_runtime_session_registration(
    observed: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
) -> None:
    """Reject same-session retries that change any provenance-bearing field."""
    observed_schema = _supported_registration_schema(
        observed.get("schema_version"),
        label="target session sidecar",
    )
    expected_for_schema = dict(expected)
    expected_config = dict(expected["config_snapshot"])
    if observed_schema != RUN_REGISTRATION_SCHEMA:
        legacy_target_run_id = _runtime_target_run_id(
            schema_version=observed_schema,
            workflow_run_id=str(expected["workflow_run_id"]),
            target_session_id=str(expected["target_session_id"]),
        )
        expected_for_schema["schema_version"] = observed_schema
        expected_for_schema["run_id"] = legacy_target_run_id
        expected_for_schema["target_run_id"] = legacy_target_run_id
        expected_config["schema_version"] = observed_schema
        expected_config["target_run_id"] = legacy_target_run_id
        if "task_sha256" not in observed:
            expected_for_schema.pop("task_sha256", None)
        observed_config = observed.get("config_snapshot")
        if (
            not isinstance(observed_config, Mapping)
            or "task_sha256" not in observed_config
        ):
            expected_config.pop("task_sha256", None)
    expected_for_schema["config_snapshot"] = expected_config
    immutable_fields = (
        "schema_version",
        "workflow_run_id",
        "run_id",
        "target_run_id",
        "target_session_id",
        "session_id",
        "task_id",
        "task",
        "target_kind",
        "join_key",
        "session_id_source",
        "completion_policy",
        "registry_path",
        "pending",
        "scope_contract",
        "config_snapshot",
        "gate",
        "runtime_run_id",
        "runtime_result_hash",
        "task_sha256",
    )
    discrepancies = [
        field
        for field in immutable_fields
        if observed.get(field) != expected_for_schema.get(field)
    ]
    if discrepancies:
        raise RuntimeError(
            "target session sidecar provenance discrepancy: "
            + ", ".join(discrepancies)
        )


def _ensure_runtime_session_state_binding(
    *,
    state: State,
    metadata: Mapping[str, Any],
    scope: ScopeContract,
) -> None:
    target_run_id = str(metadata["target_run_id"])
    target_session_id = str(metadata["target_session_id"])
    target_kind = str(metadata["target_kind"])
    task = str(metadata["task"])
    config_snapshot = dict(metadata["config_snapshot"])
    workflow_run_id = str(metadata["workflow_run_id"])
    binding_payload = {
        "schema_version": str(metadata["schema_version"]),
        "workflow_run_id": workflow_run_id,
        "target_run_id": target_run_id,
        "target_session_id": target_session_id,
        "task_id": str(metadata["task_id"]),
        "target_kind": target_kind,
        "gate": str(metadata["gate"]),
        "runtime_run_id": str(metadata["runtime_run_id"]),
        "runtime_result_hash": str(metadata["runtime_result_hash"]),
        "session_id_source": str(metadata["session_id_source"]),
        "completion_policy": str(metadata["completion_policy"]),
        "registry_path": str(metadata["registry_path"]),
    }
    task_sha256 = str(metadata.get("task_sha256") or "").strip()
    if task_sha256:
        binding_payload["task_sha256"] = task_sha256
    binding_exists = _runtime_session_binding_event_exists(
        state=state,
        workflow_run_id=workflow_run_id,
        target_session_id=target_session_id,
        expected=binding_payload,
    )
    run = state.get_run(target_run_id)
    snapshot = state.get_run_snapshot(target_run_id)
    if run is None and snapshot is None:
        state.register_run(
            run_id=target_run_id,
            session_id=target_session_id,
            rollout_path=f"pending://{target_kind}/{target_session_id}",
            task=task,
            scope=scope,
            target_kind=target_kind,
            config_snapshot=config_snapshot,
        )
        run = state.get_run(target_run_id)
        snapshot = state.get_run_snapshot(target_run_id)
    if run is None or snapshot is None:
        raise RuntimeError(
            "target session sidecar is not durably bound to State"
        )
    try:
        observed_config = json.loads(str(snapshot["config_json"] or "{}"))
        observed_scope = json.loads(
            str(snapshot["scope_contract_json"] or "{}")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "target runtime State snapshot is not valid JSON"
        ) from exc
    discrepancies: list[str] = []
    if str(run["session_id"]) != target_session_id:
        discrepancies.append("session_id")
    if str(run["task"] or "") != task:
        discrepancies.append("task")
    if str(snapshot["target_kind"] or "") != target_kind:
        discrepancies.append("target_kind")
    if observed_config != config_snapshot:
        discrepancies.append("config_snapshot")
    if observed_scope != scope.to_dict():
        discrepancies.append("scope_contract")
    if discrepancies:
        raise RuntimeError(
            "target runtime State provenance discrepancy: "
            + ", ".join(discrepancies)
        )
    if not binding_exists:
        state.write_event_once(
            run_id=workflow_run_id,
            source="supervisor",
            kind="workflow_target_session_bound",
            payload=binding_payload,
            idempotency_key=_workflow_target_session_binding_idempotency_key(
                workflow_run_id=workflow_run_id,
                target_session_id=target_session_id,
            ),
        )
    _validate_workflow_target_session_binding_event(
        state=state,
        workflow_run_id=workflow_run_id,
        target_session_id=target_session_id,
        expected=binding_payload,
        label="target runtime State provenance",
    )


def _runtime_session_binding_event_exists(
    *,
    state: State,
    workflow_run_id: str,
    target_session_id: str,
    expected: Mapping[str, Any],
) -> bool:
    exact_match = False
    after_event_id = 0
    while True:
        events = state.read_events_since(
            workflow_run_id,
            after_event_id=after_event_id,
            limit=1_000,
        )
        if not events:
            return exact_match
        for event in events:
            if (
                event.get("source") == "supervisor"
                and event.get("kind") == "workflow_target_session_bound"
            ):
                payload = event.get("payload")
                if (
                    isinstance(payload, Mapping)
                    and str(payload.get("target_session_id") or "")
                    == target_session_id
                ):
                    discrepancies = [
                        field
                        for field, expected_value in expected.items()
                        if str(payload.get(field) or "")
                        != str(expected_value)
                    ]
                    if discrepancies:
                        raise RuntimeError(
                            "target runtime binding event provenance "
                            "discrepancy: "
                            + ", ".join(discrepancies)
                        )
                    exact_match = True
        after_event_id = max(
            int(event["event_id"]) for event in events
        )


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
    _ensure_directory_durable(registry_root)
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

    _assert_session_registry_authority_clean(registry_root)
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
    with _open_launch_receipt_store(registry_root) as receipt_store:
        receipt_store.assert_namespace_current()
        for _attempt in range(16):
            launch_id = secrets.token_hex(16)
            nonce = secrets.token_urlsafe(32)
            pending_path, _ = _launch_receipt_paths(
                registry_root,
                launch_id,
            )
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
                receipt_store.exclusive_write_json(
                    "pending",
                    pending_path.name,
                    payload,
                )
            except FileExistsError:
                continue
            receipt_store.assert_namespace_current()
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


def release_launch_receipt(
    *,
    registry_dir: str | Path,
    launch_id: str,
    nonce: str,
    reason: str = "released",
    now: int | float | None = None,
) -> bool:
    """Cancel one pending launch receipt so it can never be consumed.

    Publishes a ``released`` tombstone in the consumed namespace before
    removing the pending receipt, so a late-arriving rollout carrying this
    launch identity is rejected instead of joining the workflow. Returns
    ``False`` when no pending receipt exists; raises when the receipt has
    already been claimed or the nonce does not match.
    """
    registry_root = Path(registry_dir).expanduser().resolve()
    _ensure_directory_durable(registry_root)
    normalized_launch_id = _safe_launch_id(launch_id)
    normalized_nonce = str(nonce).strip()
    with _open_launch_receipt_store(registry_root) as receipt_store:
        with _launch_receipt_consume_lock(
            receipt_store,
            normalized_launch_id,
        ):
            pending_path, consumed_path = _launch_receipt_paths(
                registry_root,
                normalized_launch_id,
            )
            expected_nonce_hash = _launch_nonce_digest(
                normalized_launch_id,
                normalized_nonce,
            )
            if receipt_store.exists("consumed", consumed_path.name):
                consumed = receipt_store.read_json(
                    "consumed",
                    consumed_path.name,
                )
                consumed_nonce_hash = str(
                    (consumed or {}).get("nonce_sha256") or ""
                ).strip()
                if (
                    consumed is not None
                    and str(consumed.get("status") or "") == "released"
                    and consumed_nonce_hash
                    and hmac.compare_digest(
                        consumed_nonce_hash,
                        expected_nonce_hash,
                    )
                ):
                    receipt_store.unlink("pending", pending_path.name)
                    receipt_store.assert_namespace_current()
                    return True
                raise LaunchReceiptError(
                    "launch receipt already claimed: "
                    f"{normalized_launch_id}"
                )
            receipt = receipt_store.read_json("pending", pending_path.name)
            if receipt is None:
                return False
            observed_nonce_hash = str(
                receipt.get("nonce_sha256") or ""
            ).strip()
            if not observed_nonce_hash or not hmac.compare_digest(
                observed_nonce_hash,
                expected_nonce_hash,
            ):
                raise LaunchReceiptError("launch receipt nonce mismatch")
            receipt_store.atomic_exclusive_write_json(
                "consumed",
                consumed_path.name,
                {
                    **receipt,
                    "status": "released",
                    "released_at": _timestamp(now),
                    "release_reason": str(reason),
                },
            )
            receipt_store.unlink("pending", pending_path.name)
            receipt_store.assert_namespace_current()
            return True


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
    runtime_run_id: str | None = None,
    runtime_result_hash: str | None = None,
    cwd: str | Path | None = None,
    rollout_path: str | None = None,
    now: int | float | None = None,
) -> dict[str, Any]:
    """Consume or recover one receipt under a process-scoped receipt lock.

    A complete ``consuming`` payload is atomically published as the ownership
    claim before the pending receipt is removed. The advisory lock keeps
    concurrent callers from treating the winner's transient claim as a crashed
    process. If the winner actually exits, the kernel releases the lock and an
    exact retry can reconcile the durable claim.
    """
    registry_root = Path(registry_dir).expanduser().resolve()
    _ensure_directory_durable(registry_root)
    normalized_launch_id = _safe_launch_id(launch_id)
    with _open_launch_receipt_store(registry_root) as receipt_store:
        with _launch_receipt_consume_lock(
            receipt_store,
            normalized_launch_id,
        ):
            return _consume_launch_receipt_locked(
                state=state,
                registry_dir=registry_root,
                receipt_store=receipt_store,
                launch_id=normalized_launch_id,
                nonce=nonce,
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                target_kind=target_kind,
                target_session_id=target_session_id,
                runtime_run_id=runtime_run_id,
                runtime_result_hash=runtime_result_hash,
                cwd=cwd,
                rollout_path=rollout_path,
                now=now,
            )


def _consume_launch_receipt_locked(
    *,
    state: State,
    registry_dir: str | Path,
    receipt_store: _LaunchReceiptStore,
    launch_id: str,
    nonce: str,
    workflow_run_id: str,
    task_id: str,
    target_kind: str,
    target_session_id: str,
    runtime_run_id: str | None = None,
    runtime_result_hash: str | None = None,
    cwd: str | Path | None = None,
    rollout_path: str | None = None,
    now: int | float | None = None,
) -> dict[str, Any]:
    """Atomically consume one matching receipt and bind its real session once."""
    receipt_store.assert_namespace_current()
    registry_root = Path(registry_dir).expanduser().resolve()
    _ensure_directory_durable(registry_root)
    normalized_launch_id = _safe_launch_id(launch_id)
    normalized_nonce = str(nonce).strip()
    normalized_workflow_run_id = str(workflow_run_id).strip()
    normalized_task_id = str(task_id).strip()
    normalized_target_kind = str(target_kind).strip()
    normalized_target_session_id = str(target_session_id).strip()
    normalized_runtime_run_id = str(runtime_run_id or "").strip()
    normalized_runtime_result_hash = str(
        runtime_result_hash or ""
    ).strip()
    if bool(normalized_runtime_run_id) != bool(
        normalized_runtime_result_hash
    ):
        raise LaunchReceiptError(
            "runtime_run_id and runtime_result_hash must be provided together"
        )
    if normalized_runtime_result_hash and not re.fullmatch(
        r"[0-9a-f]{64}",
        normalized_runtime_result_hash,
    ):
        raise LaunchReceiptError(
            "runtime_result_hash must be a canonical sha256"
        )
    normalized_cwd = _resolved_cwd(cwd)
    _session_registry_path(registry_root, normalized_target_session_id)

    pending_path, consumed_path = _launch_receipt_paths(
        registry_root,
        normalized_launch_id,
    )
    if receipt_store.exists("consumed", consumed_path.name):
        claimed = receipt_store.read_json(
            "consumed",
            consumed_path.name,
        )
        if claimed is None:
            raise LaunchReceiptError(
                "consumed launch receipt is unreadable: "
                f"{normalized_launch_id}"
            )
        status = str(claimed.get("status") or "")
        if status not in {"consuming", "consume_failed"}:
            raise LaunchReceiptError(
                f"launch receipt already consumed: {normalized_launch_id}"
            )
        _validate_recoverable_launch_receipt_payload(
            claimed,
            launch_id=normalized_launch_id,
            nonce=normalized_nonce,
            workflow_run_id=normalized_workflow_run_id,
            task_id=normalized_task_id,
            target_kind=normalized_target_kind,
            target_session_id=normalized_target_session_id,
            runtime_run_id=normalized_runtime_run_id,
            runtime_result_hash=normalized_runtime_result_hash,
            cwd=normalized_cwd,
        )
        receipt_store.unlink("pending", pending_path.name)
        receipt_store.assert_namespace_current()
        return _complete_claimed_launch_receipt(
            state=state,
            registry_root=registry_root,
            receipt_store=receipt_store,
            consumed_path=consumed_path,
            claimed=claimed,
            workflow_run_id=normalized_workflow_run_id,
            target_session_id=normalized_target_session_id,
            rollout_path=rollout_path,
            launch_id=normalized_launch_id,
            runtime_run_id=normalized_runtime_run_id,
            runtime_result_hash=normalized_runtime_result_hash,
        )
    receipt = receipt_store.read_json("pending", pending_path.name)
    if receipt is None:
        if receipt_store.exists("consumed", consumed_path.name):
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

    consumed_at = _timestamp(now)
    claimed = {
        **receipt,
        "status": "consuming",
        "consumed_at": consumed_at,
        "target_session_id": normalized_target_session_id,
        **(
            {
                "runtime_run_id": normalized_runtime_run_id,
                "runtime_result_hash": normalized_runtime_result_hash,
            }
            if normalized_runtime_run_id
            else {}
        ),
    }
    # Publish the complete recoverable claim in one exclusive namespace
    # operation. A crash after publication can leave both files present, but
    # the consumed file already contains every field needed for exact recovery.
    try:
        receipt_store.assert_namespace_current()
        receipt_store.atomic_exclusive_write_json(
            "consumed",
            consumed_path.name,
            claimed,
        )
    except FileExistsError as exc:
        raise LaunchReceiptError(
            f"launch receipt already consumed: {normalized_launch_id}"
        ) from exc
    receipt_store.unlink("pending", pending_path.name)
    receipt_store.assert_namespace_current()

    return _complete_claimed_launch_receipt(
        state=state,
        registry_root=registry_root,
        receipt_store=receipt_store,
        consumed_path=consumed_path,
        claimed=claimed,
        workflow_run_id=normalized_workflow_run_id,
        target_session_id=normalized_target_session_id,
        rollout_path=rollout_path,
        launch_id=normalized_launch_id,
        runtime_run_id=normalized_runtime_run_id,
        runtime_result_hash=normalized_runtime_result_hash,
    )


def _complete_claimed_launch_receipt(
    *,
    state: State,
    registry_root: Path,
    receipt_store: _LaunchReceiptStore,
    consumed_path: Path,
    claimed: Mapping[str, Any],
    workflow_run_id: str,
    target_session_id: str,
    rollout_path: str | None,
    launch_id: str,
    runtime_run_id: str,
    runtime_result_hash: str,
) -> dict[str, Any]:
    """Finish a durably claimed receipt, including after a process crash."""
    receipt_store.assert_namespace_current()
    try:
        bound = bind_workflow_target_session(
            state=state,
            registry_dir=registry_root,
            workflow_run_id=workflow_run_id,
            target_session_id=target_session_id,
            source=LAUNCH_RECEIPT_SOURCE,
            rollout_path=rollout_path,
            launch_id=launch_id,
            launch_receipt_path=consumed_path,
            runtime_run_id=runtime_run_id or None,
            runtime_result_hash=runtime_result_hash or None,
        )
    except Exception as exc:
        receipt_store.assert_namespace_current()
        receipt_store.atomic_write_json(
            "consumed",
            consumed_path.name,
            {
                **claimed,
                "status": "consume_failed",
                "failure_type": type(exc).__name__,
            },
        )
        raise
    completed = {
        **claimed,
        "status": "consumed",
        "session_registry_path": bound["registry_path"],
    }
    completed.pop("failure_type", None)
    receipt_store.assert_namespace_current()
    receipt_store.atomic_write_json(
        "consumed",
        consumed_path.name,
        completed,
    )
    receipt_store.assert_namespace_current()
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
    runtime_run_id: str | None = None,
    runtime_result_hash: str | None = None,
) -> dict[str, Any]:
    """Bind one pending workflow to a real target session without splitting runs."""
    registry_root = Path(registry_dir).expanduser().resolve()
    _ensure_directory_durable(registry_root)
    normalized_target_session_id = str(target_session_id).strip()
    normalized_runtime_run_id = str(runtime_run_id or "").strip()
    normalized_runtime_result_hash = str(
        runtime_result_hash or ""
    ).strip()
    if bool(normalized_runtime_run_id) != bool(
        normalized_runtime_result_hash
    ):
        raise ValueError(
            "runtime_run_id and runtime_result_hash must be provided together"
        )
    if normalized_runtime_result_hash and not re.fullmatch(
        r"[0-9a-f]{64}",
        normalized_runtime_result_hash,
    ):
        raise ValueError("runtime_result_hash must be a canonical sha256")
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
        if existing is not None:
            if existing.get("workflow_run_id") != str(workflow_run_id):
                raise RuntimeError(
                    "target session sidecar is already bound to another workflow"
                )
            _validate_bound_session_registration(
                existing,
                workflow_run_id=str(workflow_run_id),
                target_session_id=normalized_target_session_id,
                source=str(source),
                registry_path=actual_path,
                launch_id=launch_id,
                launch_receipt_path=launch_receipt_path,
                runtime_run_id=normalized_runtime_run_id,
                runtime_result_hash=normalized_runtime_result_hash,
            )
            current_run = state.get_run(str(workflow_run_id))
            if current_run is None:
                raise KeyError(f"run not found: {workflow_run_id}")
            current_session = str(current_run["session_id"] or "")
            if current_session not in {
                _pending_session_id(str(workflow_run_id)),
                normalized_target_session_id,
            }:
                raise RuntimeError(
                    "workflow run is already bound to another session"
                )
            resolved_rollout_path = (
                str(rollout_path)
                if rollout_path
                else (
                    str(current_run["rollout_path"] or "")
                    if current_session == normalized_target_session_id
                    else (
                        f"pending://{existing.get('target_kind') or 'unknown'}/"
                        f"{normalized_target_session_id}"
                    )
                )
            )
            state.bind_run_session(
                run_id=str(workflow_run_id),
                session_id=normalized_target_session_id,
                rollout_path=resolved_rollout_path,
            )
            _ensure_workflow_target_session_binding_event(
                state=state,
                metadata=existing,
                source=str(source),
                launch_id=launch_id,
                runtime_run_id=normalized_runtime_run_id,
                runtime_result_hash=normalized_runtime_result_hash,
            )
            return existing
        raise KeyError(f"pending workflow registration not found: {workflow_run_id}")

    sidecar_present = actual_path.is_file() or actual_path.is_symlink()
    existing_sidecar: dict[str, Any] | None = None
    if sidecar_present:
        existing = load_session_registration(
            registry_root,
            normalized_target_session_id,
        )
        if existing is None:
            raise RuntimeError(
                "target session sidecar exists but is unreadable; "
                "treating it as bound"
            )
        if str(existing.get("workflow_run_id") or "") != str(workflow_run_id):
            raise RuntimeError(
                "target session sidecar is already bound to another workflow"
            )
        _validate_bound_session_registration(
            existing,
            workflow_run_id=str(workflow_run_id),
            target_session_id=normalized_target_session_id,
            source=str(source),
            registry_path=actual_path,
            launch_id=launch_id,
            launch_receipt_path=launch_receipt_path,
            runtime_run_id=normalized_runtime_run_id,
            runtime_result_hash=normalized_runtime_result_hash,
        )
        existing_sidecar = existing
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
    metadata = existing_sidecar or {
        **pending,
        "target_session_id": normalized_target_session_id,
        "session_id": normalized_target_session_id,
        "join_key": normalized_target_session_id,
        "session_id_source": str(source),
        "registry_path": str(actual_path),
        "pending": False,
        "bound_at": bound_at,
        **({"launch_id": str(launch_id)} if launch_id else {}),
        **(
            {"launch_receipt_path": str(launch_receipt_path)}
            if launch_receipt_path
            else {}
        ),
        **(
            {
                "runtime_run_id": normalized_runtime_run_id,
                "runtime_result_hash": normalized_runtime_result_hash,
            }
            if normalized_runtime_run_id
            else {}
        ),
    }
    claimed = False
    if not sidecar_present:
        try:
            _exclusive_write_json(actual_path, metadata)
        except FileExistsError as exc:
            racing = load_session_registration(
                registry_root,
                normalized_target_session_id,
            )
            if racing is None:
                raise RuntimeError(
                    "target session sidecar is already bound to another "
                    "workflow"
                ) from exc
            if str(racing.get("workflow_run_id") or "") != str(
                workflow_run_id
            ):
                raise RuntimeError(
                    "target session sidecar is already bound to another "
                    "workflow"
                ) from exc
            _validate_bound_session_registration(
                racing,
                workflow_run_id=str(workflow_run_id),
                target_session_id=normalized_target_session_id,
                source=str(source),
                registry_path=actual_path,
                launch_id=launch_id,
                launch_receipt_path=launch_receipt_path,
                runtime_run_id=normalized_runtime_run_id,
                runtime_result_hash=normalized_runtime_result_hash,
            )
            existing_sidecar = racing
            metadata = racing
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
            _durable_unlink(actual_path)
        raise
    _durable_unlink(pending_path)
    _ensure_workflow_target_session_binding_event(
        state=state,
        metadata=metadata,
        source=str(source),
        launch_id=launch_id,
        runtime_run_id=normalized_runtime_run_id,
        runtime_result_hash=normalized_runtime_result_hash,
    )
    return metadata


def _ensure_workflow_target_session_binding_event(
    *,
    state: State,
    metadata: Mapping[str, Any],
    source: str,
    launch_id: str | None,
    runtime_run_id: str,
    runtime_result_hash: str,
) -> None:
    workflow_run_id = str(metadata["workflow_run_id"])
    target_session_id = str(metadata["target_session_id"])
    expected_payload = {
        "workflow_run_id": workflow_run_id,
        "target_session_id": target_session_id,
        "session_id_source": source,
        "registry_path": str(metadata["registry_path"]),
        "task_id": str(metadata.get("task_id") or ""),
        "target_kind": str(metadata.get("target_kind") or ""),
        "launch_id": str(launch_id or ""),
        "runtime_run_id": runtime_run_id,
        "runtime_result_hash": runtime_result_hash,
    }
    after_event_id = 0
    while True:
        events = state.read_events_since(
            workflow_run_id,
            after_event_id=after_event_id,
            limit=1_000,
        )
        if not events:
            break
        for event in events:
            if (
                event.get("source") != "supervisor"
                or event.get("kind") != "workflow_target_session_bound"
            ):
                continue
            payload = event.get("payload")
            if (
                not isinstance(payload, Mapping)
                or str(payload.get("target_session_id") or "")
                != target_session_id
            ):
                continue
            discrepancies = [
                field
                for field, expected in expected_payload.items()
                if str(payload.get(field) or "") != expected
            ]
            if discrepancies:
                raise RuntimeError(
                    "workflow target-session binding event provenance "
                    "discrepancy: "
                    + ", ".join(discrepancies)
                )
            return
        after_event_id = max(int(event["event_id"]) for event in events)
    state.write_event_once(
        run_id=workflow_run_id,
        source="supervisor",
        kind="workflow_target_session_bound",
        payload={
            field: value
            for field, value in expected_payload.items()
            if value
        },
        idempotency_key=_workflow_target_session_binding_idempotency_key(
            workflow_run_id=workflow_run_id,
            target_session_id=target_session_id,
        ),
    )


def _validate_bound_session_registration(
    observed: Mapping[str, Any],
    *,
    workflow_run_id: str,
    target_session_id: str,
    source: str,
    registry_path: Path,
    launch_id: str | None,
    launch_receipt_path: str | Path | None,
    runtime_run_id: str,
    runtime_result_hash: str,
) -> None:
    expected = {
        "workflow_run_id": workflow_run_id,
        "run_id": workflow_run_id,
        "target_session_id": target_session_id,
        "session_id": target_session_id,
        "join_key": target_session_id,
        "session_id_source": source,
        "registry_path": str(registry_path),
        "launch_id": str(launch_id or ""),
        "launch_receipt_path": str(launch_receipt_path or ""),
        "runtime_run_id": runtime_run_id,
        "runtime_result_hash": runtime_result_hash,
    }
    discrepancies = [
        field
        for field, expected_value in expected.items()
        if str(observed.get(field) or "") != expected_value
    ]
    if observed.get("pending") is not False:
        discrepancies.append("pending")
    if discrepancies:
        raise RuntimeError(
            "target session sidecar provenance discrepancy: "
            + ", ".join(discrepancies)
        )


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
    """Load only a complete sidecar that may authorize run ingestion."""
    registry_root = Path(registry_dir).expanduser().resolve()
    path = _session_registry_path(registry_root, session_id)
    payload = _read_registration_file(registry_root, path)
    if payload is None:
        return None
    try:
        _validate_session_registration_authority(
            payload=payload,
            path=path,
        )
    except RuntimeError:
        return None
    return payload


def load_non_authoritative_session_registration(
    registry_dir: str | Path,
    session_id: str,
) -> dict[str, Any] | None:
    """Read legacy/incomplete sidecars for inspection or migration only.

    Callers must not use this result to create a run, select a run, or ingest
    target events.
    """
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


def _assert_session_registry_authority_clean(registry_root: Path) -> None:
    """Fail before launch when an existing session join is not authoritative."""
    try:
        entries = sorted(
            (
                entry
                for entry in os.scandir(registry_root)
                if entry.name.endswith(".json")
                and not entry.name.startswith(".")
            ),
            key=lambda entry: entry.name,
        )
    except OSError as exc:
        raise LaunchReceiptError(
            "session registry namespace is unreadable"
        ) from exc
    for entry in entries:
        path = registry_root / entry.name
        payload = _read_registration_file(registry_root, path)
        try:
            _validate_session_registration_authority(
                payload=payload,
                path=path,
            )
        except RuntimeError as exc:
            raise LaunchReceiptError(
                "session registry contains malformed authority sidecar: "
                f"{entry.name}: {exc}"
            ) from exc


def _validate_session_registration_authority(
    *,
    payload: Mapping[str, Any] | None,
    path: Path,
) -> None:
    if payload is None:
        raise RuntimeError("sidecar is unreadable")
    schema_version = _supported_registration_schema(
        payload.get("schema_version"),
        label="sidecar",
    )
    if payload.get("pending") is not False:
        raise RuntimeError("pending state mismatch")
    target_session_id = str(
        payload.get("target_session_id") or ""
    ).strip()
    if not target_session_id or path.name != f"{target_session_id}.json":
        raise RuntimeError("target_session_id mismatch")
    for field, expected in (
        ("session_id", target_session_id),
        ("join_key", target_session_id),
        ("registry_path", str(path)),
    ):
        if str(payload.get(field) or "").strip() != expected:
            raise RuntimeError(f"{field} mismatch")
    for field in (
        "workflow_run_id",
        "run_id",
        "task_id",
        "task",
        "target_kind",
        "session_id_source",
        "completion_policy",
    ):
        if not str(payload.get(field) or "").strip():
            raise RuntimeError(f"{field} is missing")
    if str(payload["completion_policy"]).strip() not in _COMPLETION_POLICIES:
        raise RuntimeError("completion_policy is invalid")
    runtime_run_id = str(payload.get("runtime_run_id") or "").strip()
    runtime_result_hash = str(
        payload.get("runtime_result_hash") or ""
    ).strip()
    if bool(runtime_run_id) != bool(runtime_result_hash):
        raise RuntimeError("runtime receipt mismatch")
    if runtime_result_hash and not re.fullmatch(
        r"[0-9a-f]{64}",
        runtime_result_hash,
    ):
        raise RuntimeError("runtime_result_hash is invalid")
    scope_contract = payload.get("scope_contract")
    config_snapshot = payload.get("config_snapshot")
    if not isinstance(scope_contract, dict):
        raise RuntimeError("scope_contract is missing")
    if not isinstance(config_snapshot, dict) or not config_snapshot:
        raise RuntimeError("config_snapshot is missing")
    try:
        ScopeContract.from_dict(scope_contract)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("scope_contract is invalid") from exc
    if config_snapshot.get("schema_version") != schema_version:
        raise RuntimeError("config_snapshot schema_version mismatch")
    source = str(config_snapshot.get("source") or "").strip()
    if source not in {
        "workflow_submission",
        "workflow_runtime_session",
    }:
        raise RuntimeError("config_snapshot source mismatch")
    for field in (
        "workflow_run_id",
        "task_id",
        "target_kind",
        "cwd",
        "session_id_source",
        "completion_policy",
    ):
        if not str(config_snapshot.get(field) or "").strip():
            raise RuntimeError(f"config_snapshot {field} is missing")
    for field in (
        "workflow_run_id",
        "task_id",
        "target_kind",
        "completion_policy",
    ):
        if str(config_snapshot[field]).strip() != str(payload[field]).strip():
            raise RuntimeError(f"config_snapshot {field} mismatch")
    _resolved_cwd(config_snapshot["cwd"])
    if source == "workflow_submission":
        workflow_run_id = str(payload["workflow_run_id"]).strip()
        if str(payload["run_id"]).strip() != workflow_run_id:
            raise RuntimeError("run_id mismatch")
        if str(payload.get("target_run_id") or "").strip():
            raise RuntimeError("target_run_id mismatch")
        config_session_source = str(
            config_snapshot["session_id_source"]
        ).strip()
        config_target_session = str(
            config_snapshot.get("target_session_id") or ""
        ).strip()
        if config_session_source == PENDING_SESSION_SOURCE:
            if config_target_session:
                raise RuntimeError(
                    "config_snapshot target_session_id mismatch"
                )
            if str(payload["session_id_source"]).strip() == (
                PENDING_SESSION_SOURCE
            ):
                raise RuntimeError("session_id_source mismatch")
        else:
            if config_target_session != target_session_id:
                raise RuntimeError(
                    "config_snapshot target_session_id mismatch"
                )
            if config_session_source != str(
                payload["session_id_source"]
            ).strip():
                raise RuntimeError(
                    "config_snapshot session_id_source mismatch"
                )
        return

    for field in (
        "target_run_id",
        "target_session_id",
        "gate",
        "runtime_run_id",
        "runtime_result_hash",
    ):
        if not str(config_snapshot.get(field) or "").strip():
            raise RuntimeError(f"config_snapshot {field} is missing")
    target_run_id = str(config_snapshot["target_run_id"]).strip()
    workflow_run_id = str(config_snapshot["workflow_run_id"]).strip()
    if workflow_run_id == target_run_id:
        raise RuntimeError("workflow_run_id mismatch")
    if (
        str(payload["run_id"]).strip() != target_run_id
        or str(payload.get("target_run_id") or "").strip() != target_run_id
    ):
        raise RuntimeError("target_run_id mismatch")
    if target_run_id != _runtime_target_run_id(
        schema_version=schema_version,
        workflow_run_id=workflow_run_id,
        target_session_id=target_session_id,
    ):
        raise RuntimeError("target_run_id mismatch")
    if str(config_snapshot["session_id_source"]).strip() == (
        PENDING_SESSION_SOURCE
    ):
        raise RuntimeError("session_id_source mismatch")
    if str(config_snapshot["completion_policy"]).strip() != (
        SINGLE_TURN_COMPLETION_POLICY
    ):
        raise RuntimeError("completion_policy mismatch")
    for field in (
        "target_session_id",
        "gate",
        "runtime_run_id",
        "runtime_result_hash",
        "session_id_source",
    ):
        if str(config_snapshot[field]).strip() != str(
            payload.get(field) or ""
        ).strip():
            raise RuntimeError(f"config_snapshot {field} mismatch")
    if not re.fullmatch(
        r"[0-9a-f]{64}",
        str(config_snapshot["runtime_result_hash"]).strip(),
    ):
        raise RuntimeError("runtime_result_hash is invalid")
    _validate_runtime_task_identity(
        schema_version=schema_version,
        task=str(payload["task"]),
        config_snapshot=config_snapshot,
        payload=payload,
        label="runtime sidecar",
    )


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
    try:
        validate_pending_workflow_registration_authority(
            state=state,
            run_id=workflow_run_id,
            expected_workflow_run_id=workflow_run_id,
            expected_task_id=task_id,
            expected_target_kind=target_kind,
            expected_cwd=cwd,
        )
    except RuntimeError as exc:
        raise LaunchReceiptError(str(exc)) from exc
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
    _validate_launch_receipt_identity(
        receipt,
        launch_id=launch_id,
        nonce=nonce,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        target_kind=target_kind,
        cwd=cwd,
    )
    if receipt.get("status") != "pending":
        raise LaunchReceiptError(f"launch receipt already consumed: {launch_id}")
    try:
        issued_at = int(receipt["issued_at"])
        expires_at = int(receipt["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LaunchReceiptError("launch receipt timestamp is invalid") from exc
    if now < issued_at:
        raise LaunchReceiptError("launch receipt is not yet valid")
    if now >= expires_at:
        raise LaunchReceiptError("launch receipt expired")


def _validate_launch_receipt_identity(
    receipt: Mapping[str, Any],
    *,
    launch_id: str,
    nonce: str,
    workflow_run_id: str,
    task_id: str,
    target_kind: str,
    cwd: str | None,
) -> None:
    if receipt.get("schema_version") != LAUNCH_RECEIPT_SCHEMA:
        raise LaunchReceiptError("launch receipt schema mismatch")
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
    registered_cwd = str(receipt.get("cwd") or "").strip()
    if not registered_cwd:
        raise LaunchReceiptError("launch receipt cwd is missing")
    if cwd is not None and _resolved_cwd(registered_cwd) != cwd:
        raise LaunchReceiptError("launch receipt cwd mismatch")


def _validate_recoverable_launch_receipt_payload(
    receipt: Mapping[str, Any],
    *,
    launch_id: str,
    nonce: str,
    workflow_run_id: str,
    task_id: str,
    target_kind: str,
    target_session_id: str,
    runtime_run_id: str,
    runtime_result_hash: str,
    cwd: str,
) -> None:
    _validate_launch_receipt_identity(
        receipt,
        launch_id=launch_id,
        nonce=nonce,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        target_kind=target_kind,
        cwd=cwd,
    )
    for field, expected in (
        ("target_session_id", target_session_id),
        ("runtime_run_id", runtime_run_id),
        ("runtime_result_hash", runtime_result_hash),
    ):
        observed = str(receipt.get(field) or "").strip()
        if observed != expected:
            raise LaunchReceiptError(f"launch receipt {field} mismatch")


def _read_registration_file(
    registry_root: Path,
    path: Path,
) -> dict[str, Any] | None:
    root_descriptor = -1
    directory_descriptors: list[int] = []
    file_descriptor = -1
    try:
        candidate = path if path.is_absolute() else registry_root / path
        relative = candidate.relative_to(registry_root)
        if (
            not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            return None
        no_follow = os.O_NOFOLLOW
        read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags = (
            read_flags
            | os.O_DIRECTORY
            | no_follow
        )
        root_descriptor = os.open(registry_root, directory_flags)
        if not stat.S_ISDIR(os.fstat(root_descriptor).st_mode):
            return None
        parent_descriptor = root_descriptor
        for component in relative.parts[:-1]:
            descriptor = os.open(
                component,
                directory_flags,
                dir_fd=parent_descriptor,
            )
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                return None
            directory_descriptors.append(descriptor)
            parent_descriptor = descriptor
        file_descriptor = os.open(
            relative.parts[-1],
            read_flags | no_follow,
            dir_fd=parent_descriptor,
        )
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            return None
        with os.fdopen(file_descriptor, "r", encoding="utf-8") as handle:
            file_descriptor = -1
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)
    return payload if isinstance(payload, dict) else None


def _session_registry_path(registry_root: Path, session_id: str) -> Path:
    raw = str(session_id).strip()
    if (
        not raw
        or raw.startswith(".")
        or "\x00" in raw
        or "/" in raw
        or "\\" in raw
        or Path(raw).name != raw
    ):
        raise ValueError("target session id is not a safe registry filename")
    candidate = registry_root / f"{raw}.json"
    if candidate.parent != registry_root:
        raise ValueError("target session registry path escapes registry root")
    if candidate.is_symlink():
        try:
            candidate.resolve(strict=True).relative_to(registry_root)
        except ValueError as exc:
            raise ValueError(
                "target session registry path escapes registry root"
            ) from exc
        except OSError:
            pass
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


@contextmanager
def _launch_receipt_consume_lock(
    receipt_store: _LaunchReceiptStore,
    launch_id: str,
):
    if fcntl is None:
        raise LaunchReceiptError(
            "launch receipt consumption requires POSIX advisory locks"
        )
    normalized_launch_id = _safe_launch_id(launch_id)
    receipt_store.assert_namespace_current()
    digest = hashlib.sha256(
        normalized_launch_id.encode("utf-8")
    ).hexdigest()
    lock_name = f"{digest}.lock"
    read_flags = (
        os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(
            lock_name,
            read_flags,
            dir_fd=receipt_store.locks_fd,
        )
    except FileNotFoundError:
        try:
            created = os.open(
                lock_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=receipt_store.locks_fd,
            )
        except FileExistsError:
            pass
        else:
            os.fsync(created)
            os.close(created)
            os.fsync(receipt_store.locks_fd)
        descriptor = os.open(
            lock_name,
            read_flags,
            dir_fd=receipt_store.locks_fd,
        )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise LaunchReceiptError(
                "launch receipt lock is not a regular file"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        receipt_store.assert_namespace_current()
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def _open_launch_receipt_store(
    registry_root: Path,
) -> Iterator[_LaunchReceiptStore]:
    """Anchor all receipt namespace operations to stable directory handles."""
    root = registry_root.expanduser().absolute()
    _ensure_directory_durable(root)
    root_chain = _open_absolute_directory_chain_no_follow(root)
    opened: list[int] = []
    try:
        root_fd = root_chain[-1]
        receipt_root_fd = _open_or_create_directory_at(
            root_fd,
            _LAUNCH_RECEIPT_DIRNAME,
        )
        opened.append(receipt_root_fd)
        pending_fd = _open_or_create_directory_at(
            receipt_root_fd,
            "pending",
        )
        opened.append(pending_fd)
        consumed_fd = _open_or_create_directory_at(
            receipt_root_fd,
            "consumed",
        )
        opened.append(consumed_fd)
        locks_fd = _open_or_create_directory_at(
            receipt_root_fd,
            "locks",
        )
        opened.append(locks_fd)
        yield _LaunchReceiptStore(
            registry_root=root,
            registry_root_fd=root_fd,
            receipt_root_fd=receipt_root_fd,
            pending_fd=pending_fd,
            consumed_fd=consumed_fd,
            locks_fd=locks_fd,
        )
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)
        for descriptor in reversed(root_chain):
            os.close(descriptor)


def _open_absolute_directory_chain_no_follow(path: Path) -> list[int]:
    absolute = path.expanduser().absolute()
    if not absolute.is_absolute() or not absolute.anchor:
        raise LaunchReceiptError(
            "launch receipt registry root must be absolute"
        )
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


def _open_or_create_directory_at(parent_fd: int, name: str) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        return os.open(name, flags, dir_fd=parent_fd)


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


def _resolved_cwd(cwd: str | Path | None) -> str:
    if cwd is None:
        raise LaunchReceiptError("cwd is required")
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


def _exclusive_write_json_at(
    directory_fd: int,
    name: str,
    payload: Mapping[str, Any],
) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(directory_fd)
    except Exception:
        _durable_unlink_at(directory_fd, name)
        raise


def _atomic_exclusive_write_json_at(
    directory_fd: int,
    name: str,
    payload: Mapping[str, Any],
) -> None:
    temp_name = f".{name}.{uuid.uuid4().hex}.claim"
    try:
        _exclusive_write_json_at(directory_fd, temp_name, payload)
        os.link(
            temp_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.fsync(directory_fd)
    finally:
        _durable_unlink_at(directory_fd, temp_name)


def _atomic_write_json_at(
    directory_fd: int,
    name: str,
    payload: Mapping[str, Any],
) -> None:
    temp_name = f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        _exclusive_write_json_at(directory_fd, temp_name, payload)
        os.replace(
            temp_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        _durable_unlink_at(directory_fd, temp_name)


def _durable_unlink_at(directory_fd: int, name: str) -> bool:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return False
    os.fsync(directory_fd)
    return True


def _exclusive_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_directory_durable(path.parent)
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
        _fsync_directory(path.parent)
    except Exception:
        _durable_unlink(path)
        raise


def _replace_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_directory_durable(path.parent)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _exclusive_write_json(temp, payload)
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        _durable_unlink(temp)


def _atomic_exclusive_write_json(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    """Publish a complete JSON file without replacing an existing claim."""
    _ensure_directory_durable(path.parent)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.claim")
    try:
        _exclusive_write_json(temp, payload)
        os.link(temp, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        _durable_unlink(temp)



def _ensure_directory_durable(directory: Path) -> None:
    """Create each missing directory and persist its parent entry."""
    if directory.is_dir():
        return
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    if current.exists() and not current.is_dir():
        raise NotADirectoryError(str(current))
    for candidate in reversed(missing):
        try:
            candidate.mkdir()
        except FileExistsError:
            if not candidate.is_dir():
                raise
        _fsync_directory(candidate.parent)
    if not directory.is_dir():
        raise NotADirectoryError(str(directory))


def _durable_unlink(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    _fsync_directory(path.parent)
    return True


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise
    finally:
        os.close(descriptor)
