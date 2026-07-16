"""Append-only external checkpoints for authoritative evidence-ledger verification."""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NoReturn, Protocol, Sequence
from urllib.parse import urlsplit

from .evidence_ledger import (
    CHECKPOINT_PREDICATE_TYPE,
    CHECKPOINT_SCHEMA_VERSION,
    EVENT_IDENTITY_CHAIN_SCOPE,
    EVENT_IDENTITY_HEAD_SCOPE,
    IN_TOTO_STATEMENT_TYPE,
    LedgerError,
    LedgerVerification,
    Signer,
    _absolute_no_follow_path,
    _append_only_directory_lock,
    _append_only_file_at,
    _directory_tree_fd,
    _event_value,
    _require_canonical_sha256,
    _require_exact_integer,
    _normalize_sha256,
    _open_child_directory,
    _read_regular_file_at,
    _recover_append_only_temporary_files_at,
    canonical_json_bytes,
    compute_head_event_identity_hash,
    create_ledger_checkpoint,
    sha256_hex,
    verify_event_chain,
    verify_event_chain_structure,
)


_CHECKPOINT_KEYS = frozenset(
    {
        "_type",
        "subject",
        "predicateType",
        "predicate",
        "signatures",
        "signing_payload_sha256",
    }
)
_PREDICATE_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "head_event_id",
        "head_event_hash",
        "event_count",
        "event_identity_scope",
        "event_identity_hash",
        "previous_checkpoint_hash",
        "signer_provider_id",
        "signer_key_id",
        "signer_algorithm",
        "created_at",
        "external_anchor_ref",
    }
)
_CHECKPOINT_FILENAME = re.compile(
    r"^(?P<event_count>[0-9]{20})-(?P<head_hash>[0-9a-f]{64})\.json$"
)
_TRUSTED_PIN_FILENAME = re.compile(
    r"^(?P<event_count>[0-9]{20})-(?P<identity_hash>[0-9a-f]{64})\.json$"
)
_MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
_CHECKPOINT_IDENTITY_KEYS = frozenset(
    {
        "run_id",
        "head_event_id",
        "head_event_hash",
        "event_count",
        "event_identity_scope",
        "event_identity_hash",
        "previous_checkpoint_hash",
        "signer_provider_id",
        "signer_key_id",
        "signer_algorithm",
        "external_anchor_ref",
        "signing_payload_sha256",
    }
)
MAX_CHECKPOINT_EVENT_INTERVAL = 1_000_000
DEFAULT_TERMINAL_EVENT_KINDS = frozenset(
    {
        "arm.common_infrastructure_failed",
        "arm.completed",
        "arm.failed",
        "autoresearch_experiment_auto_run_completed",
        "autoresearch_experiment_auto_run_failed",
        "block.completed",
        "block.failed",
        "dual_agent_workflow_terminal_discrepancy",
        "dual_agent_workflow_terminal_outcome",
        "historical_operation.completed",
        "historical_operation.failed",
        "no_mistakes_validation_completed",
        "no_mistakes_validation_failed",
        "no_mistakes_validation_skipped",
        "run.cancelled",
        "run.completed",
        "run.failed",
        "supervisor_worker_blocked",
        "supervisor_worker_cancelled",
        "supervisor_worker_completed",
        "supervisor_worker_failed",
        "task.completed",
        "task.failed",
        "thread.cancelled",
        "thread.completed",
        "thread.failed",
        "tracer.completed",
        "tracer.execution.completed",
    }
)


class CheckpointIntegrityError(LedgerError):
    """A persisted checkpoint is mutable, malformed, or unsafe to read."""


class CheckpointPersistenceError(LedgerError):
    """An authoritative checkpoint lifecycle operation failed closed."""

    def __init__(self, stage: str) -> None:
        self.stage = str(stage)
        super().__init__(
            "authoritative ledger checkpoint lifecycle failed at "
            f"{self.stage}"
        )


class CheckpointSignatureVerifier(Protocol):
    """Trusted public-key or MAC verifier for one checkpoint signature."""

    def verify(self, payload: bytes, signature: Mapping[str, Any]) -> bool:
        ...


Verifier = CheckpointSignatureVerifier | Callable[[bytes, Mapping[str, Any]], bool]


@dataclass(frozen=True)
class LedgerCheckpointPolicy:
    """Bound checkpoint lag and force checkpoints at terminal event kinds."""

    max_events_between_checkpoints: int
    terminal_event_kinds: frozenset[str] = DEFAULT_TERMINAL_EVENT_KINDS

    def __post_init__(self) -> None:
        interval = self.max_events_between_checkpoints
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int)
            or not 1 <= interval <= MAX_CHECKPOINT_EVENT_INTERVAL
        ):
            raise ValueError(
                "max_events_between_checkpoints must be an integer between "
                f"1 and {MAX_CHECKPOINT_EVENT_INTERVAL}"
            )
        normalized_kinds = frozenset(
            str(kind).strip()
            for kind in self.terminal_event_kinds
            if str(kind).strip()
        )
        object.__setattr__(self, "terminal_event_kinds", normalized_kinds)

    def requires_checkpoint(
        self,
        *,
        event_count: int,
        trusted_event_count: int,
        event_kind: str,
    ) -> bool:
        if event_count < trusted_event_count:
            return True
        if event_count == trusted_event_count:
            return str(event_kind) in self.terminal_event_kinds
        return (
            str(event_kind) in self.terminal_event_kinds
            or event_count - trusted_event_count
            >= self.max_events_between_checkpoints
        )


class TrustedCheckpointPinStore(Protocol):
    """Rollback-independent persistence for trusted checkpoint identities."""

    def pin(self, identity: Mapping[str, Any]) -> None:
        """Persist ``identity`` as a non-rollbackable trusted checkpoint pin."""
        ...

    def get(
        self,
        identity: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        """Return the exact trusted identity when it remains externally pinned."""
        ...

    def latest(self, run_id: str) -> Mapping[str, Any] | None:
        """Return the authoritative latest identity for ``run_id``."""
        ...


class FilesystemTrustedCheckpointPinStore:
    """Append-only trusted pins rooted outside the checkpoint/ledger domain.

    The caller must place ``root`` in a separately protected persistence domain
    from the ledger database and checkpoint store. This class makes each pin
    immutable and detects local rollback/forks; it cannot make one filesystem
    resistant to a host administrator who rolls every domain back together.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = _absolute_no_follow_path(root)
        self._lock = threading.RLock()
        with _directory_tree_fd(
            self.root,
            create=True,
            error_type=CheckpointIntegrityError,
            label="trusted checkpoint pin store root",
        ) as root_fd:
            pins_fd = _open_child_directory(
                root_fd,
                "pins",
                create=True,
                error_type=CheckpointIntegrityError,
                label="trusted checkpoint pins directory",
            )
            if pins_fd is not None:
                os.close(pins_fd)

    def pin(self, identity: Mapping[str, Any]) -> None:
        normalized = normalize_checkpoint_identity(identity)
        value = canonical_json_bytes(normalized)
        path = self._pin_path(normalized, value=value)
        with self._lock, self._run_directory_fd(
            str(normalized["run_id"]),
            create=True,
        ) as run_fd:
            assert run_fd is not None
            with _append_only_directory_lock(
                run_fd,
                error_type=CheckpointIntegrityError,
                label="trusted checkpoint pin run directory",
            ):
                _recover_append_only_temporary_files_at(
                    run_fd,
                    error_type=CheckpointIntegrityError,
                    label="trusted checkpoint pin run directory",
                )
                existing_pins = self._load_all_from_fd(
                    run_fd,
                    expected_run_id=str(normalized["run_id"]),
                )
                if any(
                    canonical_json_bytes(existing) == value
                    for existing in existing_pins
                ):
                    return
                if existing_pins:
                    latest = existing_pins[-1]
                    latest_count = int(latest["event_count"])
                    new_count = int(normalized["event_count"])
                    if latest_count > new_count:
                        raise CheckpointIntegrityError(
                            "trusted checkpoint pin rollback refused"
                        )
                    if (
                        latest_count == new_count
                        and canonical_json_bytes(latest) != value
                    ):
                        raise CheckpointIntegrityError(
                            "trusted checkpoint pin fork refused"
                        )
                created, existing = _append_only_file_at(
                    run_fd,
                    path.name,
                    value,
                    error_type=CheckpointIntegrityError,
                    label=f"trusted checkpoint pin {path.name}",
                    _lock_held=True,
                )
                if not created and existing != value:
                    raise CheckpointIntegrityError(
                        "trusted checkpoint pin is immutable and differs"
                    )

    def get(
        self,
        identity: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        normalized = normalize_checkpoint_identity(identity)
        value = canonical_json_bytes(normalized)
        path = self._pin_path(normalized, value=value)
        with self._lock, self._run_directory_fd(
            str(normalized["run_id"]),
            create=False,
            missing_ok=True,
        ) as run_fd:
            if run_fd is None:
                return None
            with _append_only_directory_lock(
                run_fd,
                error_type=CheckpointIntegrityError,
                label="trusted checkpoint pin run directory",
            ):
                _recover_append_only_temporary_files_at(
                    run_fd,
                    error_type=CheckpointIntegrityError,
                    label="trusted checkpoint pin run directory",
                )
                if path.name not in os.listdir(run_fd):
                    return None
                observed = _read_regular_file_at(
                    run_fd,
                    path.name,
                    error_type=CheckpointIntegrityError,
                    label=f"trusted checkpoint pin {path.name}",
                    max_bytes=_MAX_CHECKPOINT_BYTES,
                )
        if observed != value:
            raise CheckpointIntegrityError(
                "trusted checkpoint pin content differs from its identity"
            )
        return dict(normalized)

    def latest(self, run_id: str) -> Mapping[str, Any] | None:
        expected_run_id = str(run_id)
        with self._lock, self._run_directory_fd(
            expected_run_id,
            create=False,
            missing_ok=True,
        ) as run_fd:
            if run_fd is None:
                return None
            with _append_only_directory_lock(
                run_fd,
                error_type=CheckpointIntegrityError,
                label="trusted checkpoint pin run directory",
            ):
                _recover_append_only_temporary_files_at(
                    run_fd,
                    error_type=CheckpointIntegrityError,
                    label="trusted checkpoint pin run directory",
                )
                identities = self._load_all_from_fd(
                    run_fd,
                    expected_run_id=expected_run_id,
                )
        return dict(identities[-1]) if identities else None

    def _load_all_from_fd(
        self,
        run_fd: int,
        *,
        expected_run_id: str,
    ) -> list[dict[str, Any]]:
        identities: list[dict[str, Any]] = []
        for filename in sorted(os.listdir(run_fd)):
            match = _TRUSTED_PIN_FILENAME.fullmatch(filename)
            if match is None:
                raise CheckpointIntegrityError(
                    f"unexpected file in trusted checkpoint pin store: "
                    f"{filename!r}"
                )
            value = _read_regular_file_at(
                run_fd,
                filename,
                error_type=CheckpointIntegrityError,
                label=f"trusted checkpoint pin {filename}",
                max_bytes=_MAX_CHECKPOINT_BYTES,
            )
            try:
                decoded = json.loads(value.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CheckpointIntegrityError(
                    f"trusted checkpoint pin {filename} is not valid JSON"
                ) from exc
            if not isinstance(decoded, Mapping):
                raise CheckpointIntegrityError(
                    f"trusted checkpoint pin {filename} must be an object"
                )
            normalized = normalize_checkpoint_identity(decoded)
            if (
                canonical_json_bytes(normalized) != value
                or normalized["run_id"] != expected_run_id
                or int(normalized["event_count"])
                != int(match.group("event_count"))
                or sha256_hex(value) != match.group("identity_hash")
            ):
                raise CheckpointIntegrityError(
                    f"trusted checkpoint pin {filename} does not match its path"
                )
            identities.append(normalized)
        identities.sort(
            key=lambda item: (
                int(item["event_count"]),
                canonical_json_bytes(item),
            )
        )
        for previous, current in zip(identities, identities[1:]):
            if int(previous["event_count"]) == int(current["event_count"]):
                raise CheckpointIntegrityError(
                    "trusted checkpoint pin fork detected"
                )
        return identities

    @contextmanager
    def _run_directory_fd(
        self,
        run_id: str,
        *,
        create: bool,
        missing_ok: bool = False,
    ) -> Iterator[int | None]:
        run_token = sha256_hex(str(run_id).encode("utf-8"))
        descriptors: list[int] = []
        with _directory_tree_fd(
            self.root,
            create=False,
            error_type=CheckpointIntegrityError,
            label="trusted checkpoint pin store root",
        ) as root_fd:
            try:
                parent_fd = root_fd
                for name, label in (
                    ("pins", "trusted checkpoint pins directory"),
                    (run_token[:2], "trusted checkpoint pin shard"),
                    (run_token, "trusted checkpoint pin run directory"),
                ):
                    child_fd = _open_child_directory(
                        parent_fd,
                        name,
                        create=create,
                        error_type=CheckpointIntegrityError,
                        label=label,
                        missing_ok=missing_ok,
                    )
                    if child_fd is None:
                        yield None
                        return
                    descriptors.append(child_fd)
                    parent_fd = child_fd
                yield descriptors[-1]
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)

    def _pin_path(
        self,
        identity: Mapping[str, Any],
        *,
        value: bytes,
    ) -> Path:
        run_token = sha256_hex(str(identity["run_id"]).encode("utf-8"))
        filename = (
            f"{int(identity['event_count']):020d}-{sha256_hex(value)}.json"
        )
        return (
            self.root
            / "pins"
            / run_token[:2]
            / run_token
            / filename
        )


@dataclass(frozen=True)
class CheckpointVerification:
    valid: bool
    run_id: str | None
    head_event_id: Any | None
    head_event_hash: str | None
    event_count: int | None
    external_anchor_ref: str | None
    failure_code: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.valid

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "run_id": self.run_id,
            "head_event_id": self.head_event_id,
            "head_event_hash": self.head_event_hash,
            "event_count": self.event_count,
            "external_anchor_ref": self.external_anchor_ref,
            "failure_code": self.failure_code,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PersistedLedgerCheckpoint:
    checkpoint: dict[str, Any]
    path: Path
    external_anchor_ref: str


@dataclass(frozen=True)
class _ParsedCheckpoint:
    checkpoint: dict[str, Any]
    signing_payload: bytes
    signatures: tuple[dict[str, Any], ...]
    run_id: str
    head_event_id: Any
    head_event_hash: str
    event_count: int
    event_identity_scope: str
    event_identity_hash: str
    previous_checkpoint_hash: str | None
    signer_provider_id: str
    signer_key_id: str
    signer_algorithm: str
    created_at: int
    external_anchor_ref: str


class _CheckpointValidationError(ValueError):
    def __init__(self, failure_code: str, detail: str) -> None:
        super().__init__(detail)
        self.failure_code = failure_code
        self.detail = detail


def checkpoint_signing_payload(checkpoint: Mapping[str, Any]) -> bytes:
    """Return exactly the in-toto statement bytes covered by signatures."""
    return canonical_json_bytes(
        {
            "_type": checkpoint["_type"],
            "subject": checkpoint["subject"],
            "predicateType": checkpoint["predicateType"],
            "predicate": checkpoint["predicate"],
        }
    )


def checkpoint_identity(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the stable identity that an external authority can pin."""
    try:
        parsed = _parse_checkpoint(checkpoint, check_signing_hash=True)
        signing_payload_sha256 = _require_canonical_sha256(
            checkpoint.get("signing_payload_sha256")
        )
    except _CheckpointValidationError as exc:
        raise CheckpointIntegrityError(
            f"{exc.failure_code}: {exc.detail}"
        ) from exc
    except ValueError as exc:
        raise CheckpointIntegrityError(str(exc)) from exc
    return {
        "run_id": parsed.run_id,
        "head_event_id": parsed.head_event_id,
        "head_event_hash": parsed.head_event_hash,
        "event_count": parsed.event_count,
        "event_identity_scope": parsed.event_identity_scope,
        "event_identity_hash": parsed.event_identity_hash,
        "previous_checkpoint_hash": parsed.previous_checkpoint_hash,
        "signer_provider_id": parsed.signer_provider_id,
        "signer_key_id": parsed.signer_key_id,
        "signer_algorithm": parsed.signer_algorithm,
        "external_anchor_ref": parsed.external_anchor_ref,
        "signing_payload_sha256": signing_payload_sha256,
    }


def normalize_checkpoint_identity(
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and canonicalize a checkpoint or trusted identity mapping."""
    return _normalize_checkpoint_identity(identity)


def _normalize_checkpoint_identity(
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    if {
        "_type",
        "predicate",
        "predicateType",
    } <= set(identity):
        return checkpoint_identity(identity)
    if set(identity) != _CHECKPOINT_IDENTITY_KEYS:
        raise CheckpointIntegrityError(
            "trusted checkpoint identity has a noncanonical shape"
        )
    try:
        run_id = _canonical_identity_text(
            identity.get("run_id"),
            field="run_id",
        )
    except ValueError as exc:
        raise CheckpointIntegrityError(
            "trusted checkpoint identity run_id is required"
        ) from exc
    try:
        head_event_id = _require_exact_integer(
            identity.get("head_event_id"),
            field="head_event_id",
            minimum=1,
        )
    except ValueError as exc:
        raise CheckpointIntegrityError(
            "trusted checkpoint identity head_event_id must be a positive integer"
        ) from exc
    external_anchor_ref_value = identity.get("external_anchor_ref")
    if type(external_anchor_ref_value) is not str:
        external_anchor_ref = ""
    else:
        external_anchor_ref = external_anchor_ref_value
    if not external_anchor_ref or not urlsplit(external_anchor_ref).scheme:
        raise CheckpointIntegrityError(
            "trusted checkpoint identity external_anchor_ref "
            "must be an absolute URI"
        )
    return {
        "run_id": run_id,
        "head_event_id": head_event_id,
        "head_event_hash": _require_canonical_sha256(
            identity.get("head_event_hash")
        ),
        "event_count": _normalize_event_count(
            identity.get("event_count")
        ),
        "event_identity_scope": _normalize_event_identity_scope(
            identity.get("event_identity_scope")
        ),
        "event_identity_hash": _require_canonical_sha256(
            identity.get("event_identity_hash")
        ),
        "previous_checkpoint_hash": (
            None
            if identity.get("previous_checkpoint_hash") is None
            else _require_canonical_sha256(
                identity.get("previous_checkpoint_hash")
            )
        ),
        "signer_provider_id": _canonical_identity_text(
            identity.get("signer_provider_id"),
            field="signer_provider_id",
        ),
        "signer_key_id": _canonical_identity_text(
            identity.get("signer_key_id"),
            field="signer_key_id",
        ),
        "signer_algorithm": _canonical_identity_text(
            identity.get("signer_algorithm"),
            field="signer_algorithm",
        ),
        "external_anchor_ref": external_anchor_ref,
        "signing_payload_sha256": _require_canonical_sha256(
            identity.get("signing_payload_sha256")
        ),
    }


def verify_ledger_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    verifier: Verifier,
    expected_run_id: str | None = None,
    expected_external_anchor_ref: str | None = None,
) -> CheckpointVerification:
    """Verify checkpoint schema, signed payload, signature, and external anchor."""
    try:
        parsed = _parse_checkpoint(checkpoint, check_signing_hash=True)
    except _CheckpointValidationError as exc:
        return CheckpointVerification(
            valid=False,
            run_id=None,
            head_event_id=None,
            head_event_hash=None,
            event_count=None,
            external_anchor_ref=None,
            failure_code=exc.failure_code,
            detail=exc.detail,
        )

    def failure(code: str, detail: str) -> CheckpointVerification:
        return CheckpointVerification(
            valid=False,
            run_id=parsed.run_id,
            head_event_id=parsed.head_event_id,
            head_event_hash=parsed.head_event_hash,
            event_count=parsed.event_count,
            external_anchor_ref=parsed.external_anchor_ref,
            failure_code=code,
            detail=detail,
        )

    if expected_run_id is not None and parsed.run_id != str(expected_run_id):
        return failure(
            "checkpoint_run_id_mismatch",
            f"expected run_id={expected_run_id!r}, observed={parsed.run_id!r}",
        )
    if (
        expected_external_anchor_ref is not None
        and parsed.external_anchor_ref != str(expected_external_anchor_ref)
    ):
        return failure(
            "checkpoint_external_anchor_mismatch",
            "checkpoint external_anchor_ref does not match its persisted location",
        )
    if not any(
        _verify_checkpoint_signature(
            verifier,
            parsed.signing_payload,
            signature,
        )
        for signature in parsed.signatures
    ):
        return failure(
            "checkpoint_signature_invalid",
            "no checkpoint signature was accepted by the trusted verifier",
        )
    return CheckpointVerification(
        valid=True,
        run_id=parsed.run_id,
        head_event_id=parsed.head_event_id,
        head_event_hash=parsed.head_event_hash,
        event_count=parsed.event_count,
        external_anchor_ref=parsed.external_anchor_ref,
    )


class LedgerCheckpointStore:
    """Filesystem-backed append-only checkpoint anchor outside the ledger DB."""

    def __init__(self, root: str | Path):
        self.root = _absolute_no_follow_path(root)
        self._lock = threading.RLock()
        with _directory_tree_fd(
            self.root,
            create=True,
            error_type=CheckpointIntegrityError,
            label="checkpoint store root",
        ) as root_fd:
            runs_fd = _open_child_directory(
                root_fd,
                "runs",
                create=True,
                error_type=CheckpointIntegrityError,
                label="checkpoint runs directory",
            )
            if runs_fd is not None:
                os.close(runs_fd)

    def external_anchor_ref(
        self,
        *,
        run_id: str,
        event_count: int,
        head_event_hash: str,
    ) -> str:
        """Return the deterministic external URI signed into a checkpoint."""
        return self._checkpoint_path(
            run_id=str(run_id),
            event_count=_normalize_event_count(event_count),
            head_event_hash=_normalize_sha256(head_event_hash),
        ).as_uri()

    def append_signed_head(
        self,
        *,
        run_id: str,
        head_event_id: Any,
        head_event_hash: str,
        event_count: int,
        signer: Signer,
        verifier: Verifier,
        created_at: int,
        event_identity_hash: str | None = None,
        signer_provider_id: str | None = None,
    ) -> PersistedLedgerCheckpoint:
        """Persist one signed head without re-signing a semantic retry."""
        normalized_run_id = str(run_id).strip()
        if not normalized_run_id:
            raise CheckpointIntegrityError("checkpoint run_id is required")
        try:
            normalized_head_event_id = _require_exact_integer(
                head_event_id,
                field="head_event_id",
                minimum=1,
            )
            normalized_count = _normalize_event_count(event_count)
            normalized_head_hash = _require_canonical_sha256(
                head_event_hash
            )
            normalized_created_at = _normalize_integer(
                created_at,
                field="created_at",
                minimum=0,
            )
        except (_CheckpointValidationError, ValueError) as exc:
            raise CheckpointIntegrityError(str(exc)) from exc
        external_anchor_ref = self.external_anchor_ref(
            run_id=normalized_run_id,
            event_count=normalized_count,
            head_event_hash=normalized_head_hash,
        )
        with self._lock, self._run_directory_fd(
            normalized_run_id,
            create=True,
        ) as run_fd:
            assert run_fd is not None
            with _append_only_directory_lock(
                run_fd,
                error_type=CheckpointIntegrityError,
                label="ledger checkpoint run directory",
            ):
                _recover_append_only_temporary_files_at(
                    run_fd,
                    error_type=CheckpointIntegrityError,
                    label="ledger checkpoint run directory",
                )
                checkpoints = self._load_all_from_fd(
                    run_fd,
                    expected_run_id=normalized_run_id,
                )
                existing = self._load_semantic_retry(
                    checkpoints=checkpoints,
                    run_id=normalized_run_id,
                    head_event_id=normalized_head_event_id,
                    head_event_hash=normalized_head_hash,
                    event_count=normalized_count,
                    event_identity_hash=event_identity_hash,
                    created_at=normalized_created_at,
                    external_anchor_ref=external_anchor_ref,
                    verifier=verifier,
                )
                if existing is not None:
                    return existing
                previous_checkpoint_hash = (
                    None
                    if not checkpoints
                    else sha256_hex(
                        canonical_json_bytes(checkpoints[-1].checkpoint)
                    )
                )
                checkpoint = create_ledger_checkpoint(
                    run_id=normalized_run_id,
                    head_event_id=normalized_head_event_id,
                    head_event_hash=normalized_head_hash,
                    event_count=normalized_count,
                    event_identity_hash=event_identity_hash,
                    previous_checkpoint_hash=previous_checkpoint_hash,
                    signer_provider_id=signer_provider_id,
                    external_anchor_ref=external_anchor_ref,
                    signer=signer,
                    created_at=normalized_created_at,
                )
                return self._append_at(
                    run_fd,
                    checkpoint,
                    verifier=verifier,
                )

    def append(
        self,
        checkpoint: Mapping[str, Any],
        *,
        verifier: Verifier,
    ) -> PersistedLedgerCheckpoint:
        """Append a verified checkpoint; exact retries are idempotent."""
        try:
            parsed = _parse_checkpoint(checkpoint, check_signing_hash=True)
        except _CheckpointValidationError as exc:
            raise CheckpointIntegrityError(
                f"{exc.failure_code}: {exc.detail}"
            ) from exc
        with self._lock, self._run_directory_fd(
            parsed.run_id,
            create=True,
        ) as run_fd:
            assert run_fd is not None
            with _append_only_directory_lock(
                run_fd,
                error_type=CheckpointIntegrityError,
                label="ledger checkpoint run directory",
            ):
                _recover_append_only_temporary_files_at(
                    run_fd,
                    error_type=CheckpointIntegrityError,
                    label="ledger checkpoint run directory",
                )
                return self._append_at(run_fd, checkpoint, verifier=verifier)

    def _append_at(
        self,
        run_fd: int,
        checkpoint: Mapping[str, Any],
        *,
        verifier: Verifier,
    ) -> PersistedLedgerCheckpoint:
        try:
            parsed = _parse_checkpoint(checkpoint, check_signing_hash=True)
        except _CheckpointValidationError as exc:
            raise CheckpointIntegrityError(
                f"{exc.failure_code}: {exc.detail}"
            ) from exc
        expected_ref = self.external_anchor_ref(
            run_id=parsed.run_id,
            event_count=parsed.event_count,
            head_event_hash=parsed.head_event_hash,
        )
        verification = verify_ledger_checkpoint(
            checkpoint,
            verifier=verifier,
            expected_run_id=parsed.run_id,
            expected_external_anchor_ref=expected_ref,
        )
        if not verification.valid:
            raise CheckpointIntegrityError(
                f"{verification.failure_code}: {verification.detail}"
            )

        value = canonical_json_bytes(dict(checkpoint))
        path = self._checkpoint_path(
            run_id=parsed.run_id,
            event_count=parsed.event_count,
            head_event_hash=parsed.head_event_hash,
        )
        history = self._load_all_from_fd(
            run_fd,
            expected_run_id=parsed.run_id,
        )
        if history:
            latest_checkpoint = history[-1].checkpoint
            latest_count = int(
                latest_checkpoint["predicate"]["event_count"]
            )
            if parsed.event_count < latest_count:
                raise CheckpointIntegrityError(
                    "checkpoint history already advances beyond the "
                    "requested stream head"
                )
            if (
                parsed.event_count > latest_count
                and parsed.previous_checkpoint_hash
                != sha256_hex(
                    canonical_json_bytes(latest_checkpoint)
                )
            ):
                raise CheckpointIntegrityError(
                    "checkpoint history link does not match the "
                    "previous checkpoint"
                )
        elif parsed.previous_checkpoint_hash is not None:
            raise CheckpointIntegrityError(
                "checkpoint history origin is missing"
            )
        for filename in sorted(os.listdir(run_fd)):
            match = _CHECKPOINT_FILENAME.fullmatch(filename)
            if match is None:
                raise CheckpointIntegrityError(
                    "unexpected file in checkpoint store: "
                    f"{filename!r}"
                )
            if (
                int(match.group("event_count")) == parsed.event_count
                and filename != path.name
            ):
                raise CheckpointIntegrityError(
                    "checkpoint fork refused at "
                    f"event_count={parsed.event_count}"
                )
        created, existing = _append_only_file_at(
            run_fd,
            path.name,
            value,
            error_type=CheckpointIntegrityError,
            label=f"ledger checkpoint {path.name}",
            _lock_held=True,
        )
        if not created and existing != value:
            existing_checkpoint = self._verified_checkpoint_bytes(
                existing,
                verifier=verifier,
                expected_run_id=parsed.run_id,
                expected_external_anchor_ref=expected_ref,
            )
            if (
                checkpoint_signing_payload(existing_checkpoint)
                != parsed.signing_payload
            ):
                raise CheckpointIntegrityError(
                    "checkpoint is immutable and already exists with "
                    "different content"
                )
        stored_value = value if created else existing
        assert stored_value is not None
        stored = json.loads(stored_value.decode("utf-8"))
        return PersistedLedgerCheckpoint(
            checkpoint=stored,
            path=path,
            external_anchor_ref=path.as_uri(),
        )

    def _load_semantic_retry(
        self,
        *,
        checkpoints: Sequence[PersistedLedgerCheckpoint],
        run_id: str,
        head_event_id: Any,
        head_event_hash: str,
        event_count: int,
        event_identity_hash: str | None,
        created_at: int,
        external_anchor_ref: str,
        verifier: Verifier,
    ) -> PersistedLedgerCheckpoint | None:
        for persisted in checkpoints:
            try:
                parsed = _parse_checkpoint(
                    persisted.checkpoint,
                    check_signing_hash=True,
                )
            except _CheckpointValidationError as exc:
                raise CheckpointIntegrityError(
                    f"{exc.failure_code}: {exc.detail}"
                ) from exc
            if parsed.event_count != event_count:
                continue
            if (
                parsed.head_event_id != head_event_id
                or parsed.head_event_hash != head_event_hash
                or parsed.created_at != created_at
                or parsed.external_anchor_ref != external_anchor_ref
                or (
                    event_identity_hash is not None
                    and (
                        parsed.event_identity_scope
                        != EVENT_IDENTITY_CHAIN_SCOPE
                        or parsed.event_identity_hash
                        != event_identity_hash
                    )
                )
            ):
                raise CheckpointIntegrityError(
                    "checkpoint is immutable and already exists with "
                    "different head semantics"
                )
            verification = verify_ledger_checkpoint(
                persisted.checkpoint,
                verifier=verifier,
                expected_run_id=run_id,
                expected_external_anchor_ref=external_anchor_ref,
            )
            if not verification.valid:
                raise CheckpointIntegrityError(
                    f"{verification.failure_code}: {verification.detail}"
                )
            return persisted
        if checkpoints:
            latest_count = int(
                checkpoints[-1].checkpoint["predicate"]["event_count"]
            )
            if latest_count > event_count:
                raise CheckpointIntegrityError(
                    "checkpoint history already advances beyond the requested "
                    "stream head"
                )
        return None

    def _verified_checkpoint_bytes(
        self,
        value: bytes,
        *,
        verifier: Verifier,
        expected_run_id: str,
        expected_external_anchor_ref: str,
    ) -> dict[str, Any]:
        try:
            checkpoint = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckpointIntegrityError(
                "existing checkpoint is not valid JSON"
            ) from exc
        if not isinstance(checkpoint, dict):
            raise CheckpointIntegrityError(
                "existing checkpoint must be a JSON object"
            )
        if canonical_json_bytes(checkpoint) != value:
            raise CheckpointIntegrityError(
                "existing checkpoint is not canonically encoded"
            )
        verification = verify_ledger_checkpoint(
            checkpoint,
            verifier=verifier,
            expected_run_id=expected_run_id,
            expected_external_anchor_ref=expected_external_anchor_ref,
        )
        if not verification.valid:
            raise CheckpointIntegrityError(
                f"{verification.failure_code}: {verification.detail}"
            )
        return checkpoint

    def _load_all_from_fd(
        self,
        run_fd: int,
        *,
        expected_run_id: str,
    ) -> list[PersistedLedgerCheckpoint]:
        persisted: list[PersistedLedgerCheckpoint] = []
        for filename in sorted(os.listdir(run_fd)):
            match = _CHECKPOINT_FILENAME.fullmatch(filename)
            if match is None:
                raise CheckpointIntegrityError(
                    f"unexpected file in checkpoint store: {filename!r}"
                )
            value = _read_regular_file_at(
                run_fd,
                filename,
                error_type=CheckpointIntegrityError,
                label=f"ledger checkpoint {filename}",
                max_bytes=_MAX_CHECKPOINT_BYTES,
            )
            try:
                checkpoint = json.loads(value.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CheckpointIntegrityError(
                    f"checkpoint {filename} is not valid JSON"
                ) from exc
            if not isinstance(checkpoint, dict):
                raise CheckpointIntegrityError(
                    f"checkpoint {filename} must be a JSON object"
                )
            if canonical_json_bytes(checkpoint) != value:
                raise CheckpointIntegrityError(
                    f"checkpoint {filename} is not canonically encoded"
                )
            try:
                parsed = _parse_checkpoint(
                    checkpoint,
                    check_signing_hash=False,
                )
            except _CheckpointValidationError as exc:
                raise CheckpointIntegrityError(
                    f"{exc.failure_code}: {exc.detail}"
                ) from exc
            declared_count = int(match.group("event_count"))
            declared_head = match.group("head_hash")
            if (
                parsed.run_id != expected_run_id
                or parsed.event_count != declared_count
                or parsed.head_event_hash != declared_head
            ):
                raise CheckpointIntegrityError(
                    f"checkpoint {filename} identity does not match its path"
                )
            path = self._checkpoint_path(
                run_id=parsed.run_id,
                event_count=parsed.event_count,
                head_event_hash=parsed.head_event_hash,
            )
            if parsed.external_anchor_ref != path.as_uri():
                raise CheckpointIntegrityError(
                    f"checkpoint {filename} external anchor does not match its path"
                )
            persisted.append(
                PersistedLedgerCheckpoint(
                    checkpoint=checkpoint,
                    path=path,
                    external_anchor_ref=path.as_uri(),
                )
            )

        persisted.sort(
            key=lambda item: (
                int(item.checkpoint["predicate"]["event_count"]),
                int(item.checkpoint["predicate"]["created_at"]),
                item.path.name,
            )
        )
        seen_counts: set[int] = set()
        previous_hash: str | None = None
        for item in persisted:
            parsed = _parse_checkpoint(
                item.checkpoint,
                check_signing_hash=False,
            )
            if parsed.event_count in seen_counts:
                raise CheckpointIntegrityError(
                    "checkpoint fork detected at "
                    f"event_count={parsed.event_count}"
                )
            seen_counts.add(parsed.event_count)
            if parsed.previous_checkpoint_hash != previous_hash:
                raise CheckpointIntegrityError(
                    "checkpoint history link is missing or does not match"
                )
            previous_hash = sha256_hex(
                canonical_json_bytes(item.checkpoint)
            )
        return persisted

    def load_all(self, run_id: str) -> list[PersistedLedgerCheckpoint]:
        """Load every checkpoint for a run, rejecting malformed or forked files."""
        normalized_run_id = str(run_id).strip()
        if not normalized_run_id:
            raise CheckpointIntegrityError("checkpoint run_id is required")
        with self._lock, self._run_directory_fd(
            normalized_run_id,
            create=False,
            missing_ok=True,
        ) as run_fd:
            if run_fd is None:
                return []
            with _append_only_directory_lock(
                run_fd,
                error_type=CheckpointIntegrityError,
                label="ledger checkpoint run directory",
            ):
                _recover_append_only_temporary_files_at(
                    run_fd,
                    error_type=CheckpointIntegrityError,
                    label="ledger checkpoint run directory",
                )
                return self._load_all_from_fd(
                    run_fd,
                    expected_run_id=normalized_run_id,
                )

    def load_latest(self, run_id: str) -> PersistedLedgerCheckpoint | None:
        checkpoints = self.load_all(run_id)
        return checkpoints[-1] if checkpoints else None

    @contextmanager
    def _run_directory_fd(
        self,
        run_id: str,
        *,
        create: bool,
        missing_ok: bool = False,
    ) -> Iterator[int | None]:
        run_token = sha256_hex(str(run_id).encode("utf-8"))
        descriptors: list[int] = []
        with _directory_tree_fd(
            self.root,
            create=False,
            error_type=CheckpointIntegrityError,
            label="checkpoint store root",
        ) as root_fd:
            try:
                parent_fd = root_fd
                for name, label in (
                    ("runs", "checkpoint runs directory"),
                    (run_token[:2], "checkpoint run shard"),
                    (run_token, "checkpoint run directory"),
                ):
                    child_fd = _open_child_directory(
                        parent_fd,
                        name,
                        create=create,
                        error_type=CheckpointIntegrityError,
                        label=label,
                        missing_ok=missing_ok,
                    )
                    if child_fd is None:
                        yield None
                        return
                    descriptors.append(child_fd)
                    parent_fd = child_fd
                yield descriptors[-1]
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)

    def _checkpoint_path(
        self,
        *,
        run_id: str,
        event_count: int,
        head_event_hash: str,
    ) -> Path:
        run_token = sha256_hex(str(run_id).encode("utf-8"))
        filename = f"{event_count:020d}-{head_event_hash}.json"
        return self.root / "runs" / run_token[:2] / run_token / filename


class LedgerCheckpointCoordinator:
    """Publish and pin verified State ledger heads under an injected policy.

    The signer, verifier, checkpoint store, and trusted pin store are supplied
    by the caller. This coordinator neither creates local key material nor
    infers that any filesystem layout is rollback-independent.
    """

    assurance = "authoritative"

    def __init__(
        self,
        *,
        signer: Signer,
        verifier: Verifier,
        checkpoint_store: LedgerCheckpointStore,
        trusted_pin_store: TrustedCheckpointPinStore,
        policy: LedgerCheckpointPolicy,
        signer_provider_id: str | None = None,
    ) -> None:
        for name, value in (
            ("signer", signer),
            ("verifier", verifier),
            ("checkpoint_store", checkpoint_store),
            ("trusted_pin_store", trusted_pin_store),
            ("policy", policy),
        ):
            if value is None:
                raise ValueError(f"{name} is required")
        self._signer = signer
        self._verifier = verifier
        self._signer_provider_id = signer_provider_id
        self.checkpoint_store = checkpoint_store
        self.trusted_pin_store = trusted_pin_store
        self.policy = policy
        self._lock = threading.RLock()
        self._trusted_identity_cache: dict[
            str,
            dict[str, Any] | None,
        ] = {}

    def coordinate_event(
        self,
        *,
        run_id: str,
        event_id: Any,
        event_count: int,
        event_kind: str,
        events_loader: Callable[[], Sequence[Mapping[str, Any] | Any]],
    ) -> PersistedLedgerCheckpoint | None:
        """Checkpoint the current verified head when the policy requires it."""
        normalized_run_id = str(run_id).strip()
        if not normalized_run_id:
            raise ValueError("run_id is required")
        if event_id is None:
            raise ValueError("event_id is required")
        if (
            isinstance(event_count, bool)
            or not isinstance(event_count, int)
            or event_count <= 0
        ):
            raise ValueError("event_count must be a positive integer")

        with self._lock:
            trusted = self._trusted_identity(
                normalized_run_id,
                refresh=False,
            )
            trusted_count = (
                int(trusted["event_count"]) if trusted is not None else 0
            )
            if trusted_count >= event_count:
                # A later trusted checkpoint already covers this committed
                # event. This can happen during startup recovery or when
                # concurrent PostgreSQL writers finish checkpoint publication
                # out of order.
                return None
            if not self.policy.requires_checkpoint(
                event_count=event_count,
                trusted_event_count=trusted_count,
                event_kind=event_kind,
            ):
                return None

            trusted = self._trusted_identity(
                normalized_run_id,
                refresh=True,
            )
            trusted_count = (
                int(trusted["event_count"]) if trusted is not None else 0
            )
            if not self.policy.requires_checkpoint(
                event_count=event_count,
                trusted_event_count=trusted_count,
                event_kind=event_kind,
            ):
                return None

            checkpoints = self._load_checkpoints(normalized_run_id)
            rows, row_verification = self._load_verified_rows(
                normalized_run_id,
                event_id=event_id,
                events_loader=events_loader,
            )
            event_identity_hash = (
                row_verification.head_event_identity_hash
            )
            if event_identity_hash is None:
                self._fail(
                    "ledger_identity_verification",
                    CheckpointIntegrityError(
                        "verified event ledger has no event identity head"
                    ),
                )
            head = rows[-1]
            actual_event_count = len(rows)
            if trusted_count > actual_event_count:
                self._fail(
                    "trusted_pin_ahead_of_ledger",
                    CheckpointIntegrityError(
                        "trusted checkpoint pin advances beyond the ledger"
                    ),
                )

            self._verify_checkpoints(
                normalized_run_id,
                checkpoints=checkpoints,
                rows=rows,
            )
            identities = [
                checkpoint_identity(persisted.checkpoint)
                for persisted in checkpoints
            ]
            if trusted is not None:
                self._confirm_trusted_identity(
                    normalized_run_id,
                    trusted=trusted,
                    persisted_identities=identities,
                )

            head_event_id = _event_value(head, "event_id")
            head_event_hash = str(_event_value(head, "event_hash"))
            head_created_at = int(_event_value(head, "ts"))
            latest = checkpoints[-1] if checkpoints else None
            if latest is not None:
                latest_identity = identities[-1]
                latest_count = int(latest_identity["event_count"])
                if latest_count > actual_event_count:
                    self._fail(
                        "checkpoint_store_ahead_of_ledger",
                        CheckpointIntegrityError(
                            "checkpoint store advances beyond the ledger"
                        ),
                    )
                if latest_count == actual_event_count:
                    if (
                        latest_identity["head_event_id"] != head_event_id
                        or latest_identity["head_event_hash"]
                        != head_event_hash
                        or latest_identity["event_identity_hash"]
                        != event_identity_hash
                    ):
                        self._fail(
                            "checkpoint_head_mismatch",
                            CheckpointIntegrityError(
                                "checkpoint head differs from the ledger"
                            ),
                        )
                    persisted = latest
                else:
                    persisted = self._append_signed_head(
                        run_id=normalized_run_id,
                        head_event_id=head_event_id,
                        head_event_hash=head_event_hash,
                        event_count=actual_event_count,
                        event_identity_hash=event_identity_hash,
                        created_at=head_created_at,
                    )
            else:
                persisted = self._append_signed_head(
                    run_id=normalized_run_id,
                    head_event_id=head_event_id,
                    head_event_hash=head_event_hash,
                    event_count=actual_event_count,
                    event_identity_hash=event_identity_hash,
                    created_at=head_created_at,
                )

            identity = checkpoint_identity(persisted.checkpoint)
            confirmed_latest = self._pin_and_confirm(identity)
            self._trusted_identity_cache[normalized_run_id] = (
                confirmed_latest
            )
            return persisted

    def verify(
        self,
        events: Sequence[Mapping[str, Any] | Any],
        *,
        expected_run_id: str,
    ) -> LedgerVerification:
        """Verify against the current externally managed trusted pin."""
        rows = list(events)
        run_id = str(expected_run_id)
        try:
            trusted = self.trusted_pin_store.latest(run_id)
            if trusted is not None:
                trusted = normalize_checkpoint_identity(trusted)
                observed = self.trusted_pin_store.get(trusted)
                if (
                    observed is None
                    or normalize_checkpoint_identity(observed) != trusted
                ):
                    return self._verification_failure(
                        rows,
                        run_id=run_id,
                        code="trusted_checkpoint_pin_missing",
                        detail=(
                            "the latest trusted checkpoint identity is not "
                            "durably retrievable"
                        ),
                    )
        except Exception:
            return self._verification_failure(
                rows,
                run_id=run_id,
                code="trusted_checkpoint_pin_store_unavailable",
                detail=(
                    "the rollback-independent trusted checkpoint pin store "
                    "could not be read"
                ),
            )
        return verify_authoritative_event_chain(
            rows,
            expected_run_id=run_id,
            checkpoint_store=self.checkpoint_store,
            verifier=self._verifier,
            trusted_latest_checkpoint=trusted,
        )

    def _trusted_identity(
        self,
        run_id: str,
        *,
        refresh: bool,
    ) -> dict[str, Any] | None:
        if not refresh and run_id in self._trusted_identity_cache:
            cached = self._trusted_identity_cache[run_id]
            return None if cached is None else dict(cached)
        try:
            observed = self.trusted_pin_store.latest(run_id)
            normalized = (
                None
                if observed is None
                else normalize_checkpoint_identity(observed)
            )
        except Exception as exc:
            self._fail("trusted_pin_read", exc)
        self._trusted_identity_cache[run_id] = normalized
        return None if normalized is None else dict(normalized)

    def _load_verified_rows(
        self,
        run_id: str,
        *,
        event_id: Any,
        events_loader: Callable[[], Sequence[Mapping[str, Any] | Any]],
    ) -> tuple[list[Mapping[str, Any] | Any], LedgerVerification]:
        try:
            rows = list(events_loader())
        except Exception as exc:
            self._fail("ledger_read", exc)
        verification = verify_event_chain_structure(
            rows,
            expected_run_id=run_id,
        )
        if (
            not verification.valid
            or verification.event_count <= 0
            or verification.head_event_id is None
            or verification.head_event_hash is None
        ):
            self._fail(
                "ledger_structure_verification",
                CheckpointIntegrityError(
                    "cannot checkpoint an empty or invalid event ledger"
                ),
            )
        if not any(
            _event_value(row, "event_id") == event_id
            for row in rows
        ):
            self._fail(
                "trigger_event_missing",
                CheckpointIntegrityError(
                    "the event that triggered checkpointing is missing"
                ),
            )
        return rows, verification

    def _load_checkpoints(
        self,
        run_id: str,
    ) -> list[PersistedLedgerCheckpoint]:
        try:
            return self.checkpoint_store.load_all(run_id)
        except Exception as exc:
            self._fail("checkpoint_store_read", exc)

    def _verify_checkpoints(
        self,
        run_id: str,
        *,
        checkpoints: Sequence[PersistedLedgerCheckpoint],
        rows: Sequence[Mapping[str, Any] | Any],
    ) -> None:
        for persisted in checkpoints:
            verification = verify_ledger_checkpoint(
                persisted.checkpoint,
                verifier=self._verifier,
                expected_run_id=run_id,
                expected_external_anchor_ref=persisted.external_anchor_ref,
            )
            if not verification.valid or verification.event_count is None:
                self._fail(
                    "checkpoint_verification",
                    CheckpointIntegrityError(
                        "a persisted checkpoint did not verify"
                    ),
                )
            checkpoint_count = int(verification.event_count)
            if checkpoint_count > len(rows):
                self._fail(
                    "checkpoint_store_ahead_of_ledger",
                    CheckpointIntegrityError(
                        "checkpoint store advances beyond the ledger"
                    ),
                )
            checkpoint_event = rows[checkpoint_count - 1]
            if (
                _event_value(checkpoint_event, "event_id")
                != verification.head_event_id
                or _event_value(checkpoint_event, "event_hash")
                != verification.head_event_hash
            ):
                self._fail(
                    "checkpoint_history_mismatch",
                    CheckpointIntegrityError(
                        "persisted checkpoint history differs from the ledger"
                    ),
                )

    def _confirm_trusted_identity(
        self,
        run_id: str,
        *,
        trusted: Mapping[str, Any],
        persisted_identities: Sequence[Mapping[str, Any]],
    ) -> None:
        normalized = normalize_checkpoint_identity(trusted)
        if normalized["run_id"] != run_id:
            self._fail(
                "trusted_pin_run_id_mismatch",
                CheckpointIntegrityError(
                    "trusted checkpoint pin belongs to another run"
                ),
            )
        if not any(
            normalize_checkpoint_identity(identity) == normalized
            for identity in persisted_identities
        ):
            self._fail(
                "checkpoint_rollback_detected",
                CheckpointIntegrityError(
                    "the externally pinned checkpoint is missing locally"
                ),
            )
        try:
            observed = self.trusted_pin_store.get(normalized)
            observed_identity = (
                None
                if observed is None
                else normalize_checkpoint_identity(observed)
            )
        except Exception as exc:
            self._fail("trusted_pin_read", exc)
        if (
            observed_identity is None
            or observed_identity != normalized
        ):
            self._fail(
                "trusted_pin_persistence",
                CheckpointIntegrityError(
                    "trusted checkpoint pin is not durably retrievable"
                ),
            )

    def _append_signed_head(
        self,
        *,
        run_id: str,
        head_event_id: Any,
        head_event_hash: str,
        event_count: int,
        event_identity_hash: str,
        created_at: int,
    ) -> PersistedLedgerCheckpoint:
        try:
            return self.checkpoint_store.append_signed_head(
                run_id=run_id,
                head_event_id=head_event_id,
                head_event_hash=head_event_hash,
                event_count=event_count,
                event_identity_hash=event_identity_hash,
                signer=self._signer,
                signer_provider_id=self._signer_provider_id,
                verifier=self._verifier,
                created_at=created_at,
            )
        except Exception as exc:
            self._fail("checkpoint_signing_or_persistence", exc)

    def _pin_and_confirm(
        self,
        identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = normalize_checkpoint_identity(identity)
        try:
            self.trusted_pin_store.pin(normalized)
        except Exception as exc:
            superseding = self._superseding_trusted_identity(normalized)
            if superseding is None:
                self._fail("trusted_pin_persistence", exc)
            return superseding
        try:
            observed = self.trusted_pin_store.get(normalized)
            latest = self.trusted_pin_store.latest(
                str(normalized["run_id"])
            )
            observed_identity = (
                None
                if observed is None
                else normalize_checkpoint_identity(observed)
            )
            latest_identity = (
                None
                if latest is None
                else normalize_checkpoint_identity(latest)
            )
        except Exception as exc:
            self._fail("trusted_pin_confirmation", exc)
        latest_is_compatible = (
            latest_identity is not None
            and latest_identity["run_id"] == normalized["run_id"]
            and int(latest_identity["event_count"])
            >= int(normalized["event_count"])
            and (
                int(latest_identity["event_count"])
                != int(normalized["event_count"])
                or latest_identity == normalized
            )
        )
        if (
            observed_identity is None
            or observed_identity != normalized
            or not latest_is_compatible
        ):
            self._fail(
                "trusted_pin_confirmation",
                CheckpointIntegrityError(
                    "trusted checkpoint pin persistence was not confirmed"
                ),
            )
        assert latest_identity is not None
        return latest_identity

    def _superseding_trusted_identity(
        self,
        normalized: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        run_id = str(normalized["run_id"])
        try:
            latest = self.trusted_pin_store.latest(run_id)
            latest_identity = (
                None
                if latest is None
                else normalize_checkpoint_identity(latest)
            )
        except Exception:
            return None
        if (
            latest_identity is None
            or latest_identity["run_id"] != normalized["run_id"]
            or int(latest_identity["event_count"])
            <= int(normalized["event_count"])
        ):
            return None
        try:
            observed = self.trusted_pin_store.get(latest_identity)
            observed_identity = (
                None
                if observed is None
                else normalize_checkpoint_identity(observed)
            )
        except Exception:
            return None
        if observed_identity != latest_identity:
            return None
        try:
            persisted_identities = [
                checkpoint_identity(persisted.checkpoint)
                for persisted in self.checkpoint_store.load_all(run_id)
            ]
        except Exception:
            return None
        if (
            dict(normalized) not in persisted_identities
            or latest_identity not in persisted_identities
        ):
            return None
        return latest_identity

    @staticmethod
    def _verification_failure(
        rows: Sequence[Mapping[str, Any] | Any],
        *,
        run_id: str,
        code: str,
        detail: str,
    ) -> LedgerVerification:
        head = rows[-1] if rows else None
        return LedgerVerification(
            valid=False,
            run_id=run_id,
            event_count=len(rows),
            head_event_id=(
                _event_value(head, "event_id")
                if head is not None
                else None
            ),
            head_event_hash=(
                str(_event_value(head, "event_hash"))
                if head is not None
                else None
            ),
            expected_head_hash=None,
            truncation_checked=False,
            authoritative_head_verified=False,
            failure_code=code,
            failure_event_id=None,
            detail=detail,
        )

    @staticmethod
    def _fail(stage: str, exc: BaseException) -> NoReturn:
        raise CheckpointPersistenceError(stage) from exc


def verify_authoritative_event_chain(
    events: Sequence[Mapping[str, Any] | Any],
    *,
    expected_run_id: str,
    checkpoint_store: LedgerCheckpointStore,
    verifier: Verifier,
    trusted_latest_checkpoint: Mapping[str, Any] | None = None,
    require_local_latest: bool = True,
) -> LedgerVerification:
    """Fail closed unless the exact stream head has a persisted valid checkpoint."""
    rows = list(events)
    run_id = str(expected_run_id)
    trusted_identity: dict[str, Any] | None = None

    def failure(
        code: str,
        detail: str,
        *,
        expected_head_hash: str | None = None,
        truncation_checked: bool = False,
    ) -> LedgerVerification:
        head = rows[-1] if rows else None
        observed_hash = (
            _event_value(head, "event_hash") if head is not None else None
        )
        return LedgerVerification(
            valid=False,
            run_id=run_id,
            event_count=len(rows),
            head_event_id=(
                _event_value(head, "event_id") if head is not None else None
            ),
            head_event_hash=(
                str(observed_hash) if observed_hash is not None else None
            ),
            expected_head_hash=expected_head_hash,
            truncation_checked=truncation_checked,
            failure_code=code,
            failure_event_id=None,
            detail=detail,
        )

    if trusted_latest_checkpoint is None:
        return failure(
            "trusted_checkpoint_required",
            (
                "authoritative verification requires an explicit latest "
                "checkpoint pin from a rollback-independent trust domain"
            ),
        )
    try:
        trusted_identity = _normalize_checkpoint_identity(
            trusted_latest_checkpoint
        )
    except (CheckpointIntegrityError, ValueError) as exc:
        return failure(
            "trusted_checkpoint_identity_invalid",
            str(exc),
        )
    if trusted_identity["run_id"] != run_id:
        return failure(
            "trusted_checkpoint_run_id_mismatch",
            (
                f"expected run_id={run_id!r}, observed="
                f"{trusted_identity['run_id']!r}"
            ),
            expected_head_hash=trusted_identity["head_event_hash"],
            truncation_checked=True,
        )

    try:
        checkpoints = checkpoint_store.load_all(run_id)
    except CheckpointIntegrityError as exc:
        return failure("checkpoint_store_invalid", str(exc))
    if not checkpoints:
        return failure(
            "checkpoint_rollback_detected",
            "the externally pinned latest checkpoint is missing locally",
            expected_head_hash=trusted_identity["head_event_hash"],
            truncation_checked=True,
        )

    verified: list[tuple[PersistedLedgerCheckpoint, CheckpointVerification]] = []
    for persisted in checkpoints:
        checkpoint_verification = verify_ledger_checkpoint(
            persisted.checkpoint,
            verifier=verifier,
            expected_run_id=run_id,
            expected_external_anchor_ref=persisted.external_anchor_ref,
        )
        if not checkpoint_verification.valid:
            return failure(
                checkpoint_verification.failure_code
                or "checkpoint_verification_failed",
                checkpoint_verification.detail or "checkpoint verification failed",
            )
        verified.append((persisted, checkpoint_verification))

    latest_persisted, latest = verified[-1]
    selected_persisted = latest_persisted
    selected = latest
    if trusted_identity is not None and require_local_latest:
        try:
            local_identity = checkpoint_identity(
                latest_persisted.checkpoint
            )
        except CheckpointIntegrityError as exc:
            return failure("checkpoint_store_invalid", str(exc))
        if local_identity != trusted_identity:
            rollback = (
                int(local_identity["event_count"])
                < int(trusted_identity["event_count"])
            )
            return failure(
                (
                    "checkpoint_rollback_detected"
                    if rollback
                    else "trusted_checkpoint_identity_mismatch"
                ),
                (
                    "local latest checkpoint does not match the externally "
                    "pinned latest checkpoint identity"
                ),
                expected_head_hash=trusted_identity["head_event_hash"],
                truncation_checked=True,
            )
    elif trusted_identity is not None:
        selected_pair = next(
            (
                (persisted, checkpoint)
                for persisted, checkpoint in verified
                if checkpoint_identity(persisted.checkpoint) == trusted_identity
            ),
            None,
        )
        if selected_pair is None:
            latest_count = int(
                checkpoint_identity(latest_persisted.checkpoint)["event_count"]
            )
            trusted_count = int(trusted_identity["event_count"])
            return failure(
                (
                    "checkpoint_rollback_detected"
                    if latest_count < trusted_count
                    else "trusted_checkpoint_missing_locally"
                ),
                (
                    "the externally pinned checkpoint is missing from the "
                    "local checkpoint history"
                ),
                expected_head_hash=trusted_identity["head_event_hash"],
                truncation_checked=True,
            )
        selected_persisted, selected = selected_pair

    assert selected.head_event_hash is not None
    assert selected.event_count is not None
    if len(rows) != selected.event_count:
        return failure(
            "checkpoint_event_count_mismatch",
            f"checkpoint event_count={selected.event_count}, observed={len(rows)}",
            expected_head_hash=selected.head_event_hash,
            truncation_checked=True,
        )

    selected_identity = checkpoint_identity(
        selected_persisted.checkpoint
    )
    expected_identity_hash = (
        str(selected_identity["event_identity_hash"])
        if selected_identity["event_identity_scope"]
        == EVENT_IDENTITY_CHAIN_SCOPE
        else None
    )
    chain = verify_event_chain(
        rows,
        expected_head_hash=selected.head_event_hash,
        expected_event_identity_hash=expected_identity_hash,
        expected_run_id=run_id,
    )
    if not chain.valid:
        return chain
    if chain.head_event_id != selected.head_event_id:
        return failure(
            "checkpoint_head_event_id_mismatch",
            (
                f"checkpoint head_event_id={selected.head_event_id!r}, "
                f"observed={chain.head_event_id!r}"
            ),
            expected_head_hash=selected.head_event_hash,
            truncation_checked=True,
        )
    if (
        selected_identity["event_identity_scope"]
        == EVENT_IDENTITY_HEAD_SCOPE
    ):
        computed_head_identity = compute_head_event_identity_hash(
            run_id=run_id,
            event_count=int(selected.event_count),
            head_event_id=int(chain.head_event_id),
            head_event_hash=selected.head_event_hash,
        )
        if (
            computed_head_identity
            != selected_identity["event_identity_hash"]
        ):
            return failure(
                "expected_event_identity_hash_mismatch",
                "checkpoint head event identity differs from the ledger",
                expected_head_hash=selected.head_event_hash,
                truncation_checked=True,
            )
    applicable = [
        pair
        for pair in verified
        if int(pair[1].event_count or 0) <= int(selected.event_count)
    ]
    for _, checkpoint in applicable:
        assert checkpoint.event_count is not None
        assert checkpoint.head_event_hash is not None
        checkpoint_event = rows[checkpoint.event_count - 1]
        observed_checkpoint_hash = _event_value(checkpoint_event, "event_hash")
        observed_checkpoint_id = _event_value(checkpoint_event, "event_id")
        if (
            observed_checkpoint_hash != checkpoint.head_event_hash
            or observed_checkpoint_id != checkpoint.head_event_id
        ):
            return failure(
                "checkpoint_history_mismatch",
                (
                    f"checkpoint event_count={checkpoint.event_count} does not "
                    "match the corresponding event in the verified chain"
                ),
                expected_head_hash=selected.head_event_hash,
                truncation_checked=True,
            )
    return replace(
        chain,
        authoritative_head_verified=True,
        external_anchor_ref=selected.external_anchor_ref,
    )


verify_event_chain_authoritatively = verify_authoritative_event_chain


def _parse_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    check_signing_hash: bool,
) -> _ParsedCheckpoint:
    if not isinstance(checkpoint, Mapping):
        raise _CheckpointValidationError(
            "checkpoint_invalid_shape",
            "checkpoint must be an object",
        )
    body = dict(checkpoint)
    if set(body) != _CHECKPOINT_KEYS:
        raise _CheckpointValidationError(
            "checkpoint_invalid_shape",
            "checkpoint has missing or unsigned top-level fields",
        )
    if body.get("_type") != IN_TOTO_STATEMENT_TYPE:
        raise _CheckpointValidationError(
            "checkpoint_invalid_statement_type",
            "checkpoint _type is not the supported in-toto statement",
        )
    if body.get("predicateType") != CHECKPOINT_PREDICATE_TYPE:
        raise _CheckpointValidationError(
            "checkpoint_invalid_predicate_type",
            "checkpoint predicateType is not supported",
        )
    predicate = body.get("predicate")
    if not isinstance(predicate, Mapping) or set(predicate) != _PREDICATE_KEYS:
        raise _CheckpointValidationError(
            "checkpoint_invalid_predicate",
            "checkpoint predicate has an invalid shape",
        )
    if predicate.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise _CheckpointValidationError(
            "checkpoint_invalid_schema_version",
            "checkpoint schema_version is not supported",
        )
    try:
        run_id = _canonical_identity_text(
            predicate.get("run_id"),
            field="run_id",
        )
    except ValueError as exc:
        raise _CheckpointValidationError(
            "checkpoint_invalid_run_id",
            "checkpoint run_id is required",
        ) from exc
    try:
        head_event_id = _require_exact_integer(
            predicate.get("head_event_id"),
            field="head_event_id",
            minimum=1,
        )
    except ValueError as exc:
        raise _CheckpointValidationError(
            "checkpoint_invalid_head_event_id",
            "checkpoint head_event_id must be a positive integer",
        ) from exc
    try:
        head_event_hash = _require_canonical_sha256(
            predicate.get("head_event_hash")
        )
    except ValueError as exc:
        raise _CheckpointValidationError(
            "checkpoint_invalid_head_hash",
            str(exc),
        ) from exc
    event_count = _normalize_event_count(predicate.get("event_count"))
    try:
        event_identity_scope = _normalize_event_identity_scope(
            predicate.get("event_identity_scope")
        )
        event_identity_hash = _require_canonical_sha256(
            predicate.get("event_identity_hash")
        )
        previous_checkpoint_hash = (
            None
            if predicate.get("previous_checkpoint_hash") is None
            else _require_canonical_sha256(
                predicate.get("previous_checkpoint_hash")
            )
        )
        signer_provider_id = _canonical_identity_text(
            predicate.get("signer_provider_id"),
            field="signer_provider_id",
        )
        signer_key_id = _canonical_identity_text(
            predicate.get("signer_key_id"),
            field="signer_key_id",
        )
        signer_algorithm = _canonical_identity_text(
            predicate.get("signer_algorithm"),
            field="signer_algorithm",
        )
    except ValueError as exc:
        raise _CheckpointValidationError(
            "checkpoint_invalid_identity",
            str(exc),
        ) from exc
    created_at = _normalize_integer(
        predicate.get("created_at"),
        field="created_at",
        minimum=0,
    )
    external_anchor_value = predicate.get("external_anchor_ref")
    external_anchor_ref = (
        external_anchor_value
        if type(external_anchor_value) is str
        else ""
    )
    if not external_anchor_ref or not urlsplit(external_anchor_ref).scheme:
        raise _CheckpointValidationError(
            "checkpoint_invalid_external_anchor",
            "checkpoint external_anchor_ref must be an absolute URI",
        )

    subject = body.get("subject")
    if not isinstance(subject, list) or len(subject) != 1:
        raise _CheckpointValidationError(
            "checkpoint_invalid_subject",
            "checkpoint must contain exactly one subject",
        )
    subject_item = subject[0]
    if not isinstance(subject_item, Mapping):
        raise _CheckpointValidationError(
            "checkpoint_invalid_subject",
            "checkpoint subject must be an object",
        )
    digest = subject_item.get("digest")
    if (
        subject_item.get("name") != f"evidence-ledger/run/{run_id}"
        or not isinstance(digest, Mapping)
        or digest.get("sha256") != head_event_hash
    ):
        raise _CheckpointValidationError(
            "checkpoint_subject_mismatch",
            "checkpoint subject does not match its run and head hash",
        )

    signatures = body.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise _CheckpointValidationError(
            "checkpoint_signature_missing",
            "checkpoint must contain at least one signature",
        )
    normalized_signatures: list[dict[str, Any]] = []
    for signature in signatures:
        if not isinstance(signature, Mapping):
            raise _CheckpointValidationError(
                "checkpoint_signature_invalid",
                "checkpoint signature must be an object",
            )
        normalized_signature = dict(signature)
        if any(
            not isinstance(normalized_signature.get(field), str)
            or not str(normalized_signature[field]).strip()
            for field in ("key_id", "algorithm", "signature")
        ):
            raise _CheckpointValidationError(
                "checkpoint_signature_invalid",
                "checkpoint signature metadata is incomplete",
            )
        if (
            normalized_signature["key_id"] != signer_key_id
            or normalized_signature["algorithm"] != signer_algorithm
        ):
            raise _CheckpointValidationError(
                "checkpoint_signature_identity_mismatch",
                "checkpoint signature identity differs from its signed predicate",
            )
        normalized_signatures.append(normalized_signature)

    try:
        signing_payload = checkpoint_signing_payload(body)
        signing_payload_hash = _require_canonical_sha256(
            body.get("signing_payload_sha256")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _CheckpointValidationError(
            "checkpoint_signing_payload_invalid",
            str(exc),
        ) from exc
    if check_signing_hash and sha256_hex(signing_payload) != signing_payload_hash:
        raise _CheckpointValidationError(
            "checkpoint_signing_payload_hash_mismatch",
            "checkpoint signed payload does not match signing_payload_sha256",
        )
    return _ParsedCheckpoint(
        checkpoint=body,
        signing_payload=signing_payload,
        signatures=tuple(normalized_signatures),
        run_id=run_id,
        head_event_id=head_event_id,
        head_event_hash=head_event_hash,
        event_count=event_count,
        event_identity_scope=event_identity_scope,
        event_identity_hash=event_identity_hash,
        previous_checkpoint_hash=previous_checkpoint_hash,
        signer_provider_id=signer_provider_id,
        signer_key_id=signer_key_id,
        signer_algorithm=signer_algorithm,
        created_at=created_at,
        external_anchor_ref=external_anchor_ref,
    )


def _canonical_identity_text(value: Any, *, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
    ):
        raise ValueError(f"checkpoint {field} is not canonical")
    return value


def _normalize_event_identity_scope(value: Any) -> str:
    if value not in {
        EVENT_IDENTITY_CHAIN_SCOPE,
        EVENT_IDENTITY_HEAD_SCOPE,
    }:
        raise ValueError("checkpoint event_identity_scope is not supported")
    return str(value)


def _normalize_event_count(value: Any) -> int:
    return _normalize_integer(
        value,
        field="event_count",
        minimum=1,
        maximum=(10**20) - 1,
    )


def _normalize_integer(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _CheckpointValidationError(
            f"checkpoint_invalid_{field}",
            f"checkpoint {field} must be an integer",
        )
    if value < minimum or (maximum is not None and value > maximum):
        raise _CheckpointValidationError(
            f"checkpoint_invalid_{field}",
            f"checkpoint {field} is outside the supported range",
        )
    return value


def _verify_checkpoint_signature(
    verifier: Verifier,
    payload: bytes,
    signature: Mapping[str, Any],
) -> bool:
    expected_key_id = getattr(verifier, "key_id", None)
    if expected_key_id is not None and signature.get("key_id") != str(
        expected_key_id
    ):
        return False
    expected_algorithm = getattr(verifier, "algorithm", None)
    if expected_algorithm is not None and signature.get("algorithm") != str(
        expected_algorithm
    ):
        return False
    verify = getattr(verifier, "verify", None)
    try:
        result = (
            verify(payload, signature)
            if callable(verify)
            else verifier(payload, signature)  # type: ignore[misc]
        )
    except Exception:
        return False
    return result is True


__all__ = [
    "CheckpointIntegrityError",
    "CheckpointPersistenceError",
    "CheckpointSignatureVerifier",
    "CheckpointVerification",
    "DEFAULT_TERMINAL_EVENT_KINDS",
    "FilesystemTrustedCheckpointPinStore",
    "LedgerCheckpointCoordinator",
    "LedgerCheckpointPolicy",
    "LedgerCheckpointStore",
    "MAX_CHECKPOINT_EVENT_INTERVAL",
    "PersistedLedgerCheckpoint",
    "TrustedCheckpointPinStore",
    "Verifier",
    "checkpoint_identity",
    "checkpoint_signing_payload",
    "normalize_checkpoint_identity",
    "verify_authoritative_event_chain",
    "verify_event_chain_authoritatively",
    "verify_ledger_checkpoint",
]
