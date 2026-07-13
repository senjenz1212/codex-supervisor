"""Audited public service for rerun, regrade, and replay operations.

The three operations intentionally share one entry point but not semantics:

* rerun creates a new execution and therefore a new run identity;
* regrade keeps the frozen result fixed and appends a verifier revision;
* replay performs deterministic recomputation without executing an agent.

Every request and result is content-addressed and recorded on its own event
stream.  Reusing an idempotency key with different content fails closed.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Protocol

from .redaction import redact


HISTORICAL_OPERATION_REQUEST_SCHEMA_VERSION = (
    "supervisor-historical-operation-request/v1"
)
HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION = (
    "supervisor-historical-operation-receipt/v1"
)
HISTORICAL_OPERATION_EVENT_SOURCE = "historical_evaluation"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESULT_IDENTITY_FIELDS = frozenset(
    {
        "artifact_ref",
        "artifact_sha256",
        "new_run_id",
        "run_manifest_ref",
        "run_manifest_sha256",
        "grade_revision_id",
        "supersedes_grade_id",
        "verifier_id",
        "verifier_version",
        "verifier_config_sha256",
        "verifier_implementation_sha256",
        "source_frozen_result_sha256",
        "source_manifest_sha256",
        "replay_schema_version",
    }
)


class HistoricalEvaluationError(RuntimeError):
    """Base error for historical-operation contract failures."""


class HistoricalEvidenceError(HistoricalEvaluationError):
    """Pinned source or result bytes are unavailable or changed."""


class HistoricalSemanticsError(HistoricalEvaluationError):
    """An executor returned a result that violates operation semantics."""


class HistoricalIdempotencyConflict(HistoricalEvaluationError):
    """One idempotency key was reused for a different request."""


class HistoricalOperationPreviouslyFailed(HistoricalEvaluationError):
    """The same durable operation identity already ended in failure."""


class HistoricalOperationInProgress(HistoricalEvaluationError):
    """Another process already owns the same durable operation identity."""


class HistoricalOperationIndeterminate(HistoricalEvaluationError):
    """A stale operation may have crossed an unrecoverable side-effect boundary."""


class HistoricalOperation(str, Enum):
    RERUN = "rerun"
    REGRADE = "regrade"
    REPLAY = "replay"


@dataclass(frozen=True)
class HistoricalSource:
    source_run_id: str
    task_id: str
    source_manifest_ref: str
    source_manifest_sha256: str
    frozen_result_ref: str
    frozen_result_sha256: str
    current_grade_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "source_run_id",
            "task_id",
            "source_manifest_ref",
            "frozen_result_ref",
        ):
            normalized = _required_text(
                field_name,
                getattr(self, field_name),
            )
            if redact(normalized) != normalized:
                raise HistoricalSemanticsError(
                    f"{field_name} may not contain secret-shaped values"
                )
            object.__setattr__(
                self,
                field_name,
                normalized,
            )
        for field_name in (
            "source_manifest_sha256",
            "frozen_result_sha256",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_sha256(field_name, getattr(self, field_name)),
            )
        if self.current_grade_id is not None:
            normalized_grade_id = _required_text(
                "current_grade_id",
                self.current_grade_id,
            )
            if redact(normalized_grade_id) != normalized_grade_id:
                raise HistoricalSemanticsError(
                    "current_grade_id may not contain secret-shaped values"
                )
            object.__setattr__(
                self,
                "current_grade_id",
                normalized_grade_id,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_run_id": self.source_run_id,
            "task_id": self.task_id,
            "source_manifest_ref": self.source_manifest_ref,
            "source_manifest_sha256": self.source_manifest_sha256,
            "frozen_result_ref": self.frozen_result_ref,
            "frozen_result_sha256": self.frozen_result_sha256,
            "current_grade_id": self.current_grade_id,
        }


@dataclass(frozen=True)
class HistoricalOperationRequest:
    operation: HistoricalOperation
    source: HistoricalSource
    idempotency_key: str
    requested_by: str
    reason: str
    options: Mapping[str, Any]
    schema_version: str = HISTORICAL_OPERATION_REQUEST_SCHEMA_VERSION
    _request_hash: str = field(init=False, repr=False)
    _operation_id: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_schema_version(
            field_name="request.schema_version",
            observed=self.schema_version,
            expected=HISTORICAL_OPERATION_REQUEST_SCHEMA_VERSION,
            label="historical operation request",
        )
        if not isinstance(self.operation, HistoricalOperation):
            object.__setattr__(
                self,
                "operation",
                HistoricalOperation(str(self.operation)),
            )
        idempotency_key = _required_text(
            "idempotency_key",
            self.idempotency_key,
        )
        if redact(idempotency_key) != idempotency_key:
            raise HistoricalSemanticsError(
                "idempotency_key may not contain secret-shaped values"
            )
        object.__setattr__(self, "idempotency_key", idempotency_key)
        for field_name in ("requested_by", "reason"):
            normalized = _required_text(
                field_name,
                getattr(self, field_name),
            )
            object.__setattr__(
                self,
                field_name,
                str(redact(normalized)),
            )
        normalized_options = _normalise_json_mapping(
            self.options,
            field="options",
        )
        object.__setattr__(
            self,
            "options",
            _deep_freeze(
                _normalise_json_mapping(
                    redact(normalized_options),
                    field="options",
                )
            ),
        )
        canonical = self.to_dict()
        object.__setattr__(self, "_request_hash", _sha256_json(canonical))
        digest = sha256(
            (
                self.schema_version
                + "\x00"
                + self.idempotency_key
            ).encode("utf-8")
        ).hexdigest()
        object.__setattr__(
            self,
            "_operation_id",
            f"historical-{digest[:32]}",
        )

    @property
    def request_hash(self) -> str:
        return self._request_hash

    @property
    def operation_id(self) -> str:
        return self._operation_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation": self.operation.value,
            "source": self.source.to_dict(),
            "idempotency_key": self.idempotency_key,
            "requested_by": self.requested_by,
            "reason": self.reason,
            "options": _normalise_json(self.options),
        }


@dataclass(frozen=True)
class HistoricalOperationReceipt:
    operation_id: str
    operation: HistoricalOperation
    request_hash: str
    source_run_id: str
    task_id: str
    result_ref: str
    result_sha256: str
    result: Mapping[str, Any]
    requested_event_id: int
    completed_event_id: int
    receipt_hash: str
    schema_version: str = HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema_version(
            field_name="receipt.schema_version",
            observed=self.schema_version,
            expected=HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION,
            label="historical operation receipt",
        )
        if not isinstance(self.operation, HistoricalOperation):
            object.__setattr__(
                self,
                "operation",
                HistoricalOperation(str(self.operation)),
            )
        for field_name in (
            "operation_id",
            "source_run_id",
            "task_id",
            "result_ref",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(field_name, getattr(self, field_name)),
            )
        for field_name in (
            "request_hash",
            "result_sha256",
            "receipt_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_sha256(field_name, getattr(self, field_name)),
            )
        requested_event_id = _required_positive_int(
            "requested_event_id",
            self.requested_event_id,
        )
        completed_event_id = _required_positive_int(
            "completed_event_id",
            self.completed_event_id,
        )
        if completed_event_id <= requested_event_id:
            raise HistoricalSemanticsError(
                "completed_event_id must follow requested_event_id"
            )
        object.__setattr__(
            self,
            "requested_event_id",
            requested_event_id,
        )
        object.__setattr__(
            self,
            "completed_event_id",
            completed_event_id,
        )
        object.__setattr__(
            self,
            "result",
            _deep_freeze(
                _stored_result_mapping(self.result)
            ),
        )
        expected_hash = _sha256_json(self._receipt_body())
        if self.receipt_hash != expected_hash:
            raise HistoricalEvidenceError(
                "historical completion receipt hash does not verify"
            )

    def _receipt_body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "operation": self.operation.value,
            "request_hash": self.request_hash,
            "source_run_id": self.source_run_id,
            "task_id": self.task_id,
            "result_ref": self.result_ref,
            "result_sha256": self.result_sha256,
            "result": _normalise_json(self.result),
            "requested_event_id": self.requested_event_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "operation": self.operation.value,
            "request_hash": self.request_hash,
            "source_run_id": self.source_run_id,
            "task_id": self.task_id,
            "result_ref": self.result_ref,
            "result_sha256": self.result_sha256,
            "result": _normalise_json(self.result),
            "requested_event_id": self.requested_event_id,
            "completed_event_id": self.completed_event_id,
            "receipt_hash": self.receipt_hash,
        }


HistoricalExecutor = Callable[
    [HistoricalOperationRequest],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]
EvidenceResolver = Callable[
    [str],
    bytes | bytearray | memoryview | None,
]


class HistoricalState(Protocol):
    @property
    def event_ledger_assurance(self) -> str:
        ...

    def reserve_historical_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        operation: str,
    ) -> tuple[Mapping[str, Any], bool]:
        ...

    def complete_historical_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        status: str,
        terminal_event_id: int,
    ) -> int:
        ...

    def write_historical_operation_event(
        self,
        *,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        ts: int | None = None,
    ) -> int:
        ...

    def read_events_since(
        self,
        run_id: str,
        after_event_id: int | None = 0,
        limit: int = 100,
    ) -> list[Mapping[str, Any]]:
        ...

    def ensure_event_checkpoint(
        self,
        *,
        run_id: str,
        event_id: int,
        event_kind: str,
    ) -> None:
        ...

    def verify_event_ledger(self, run_id: str) -> Any:
        ...


class HistoricalEvaluationService:
    """Execute historical operations behind one fail-closed audit boundary."""

    def __init__(
        self,
        *,
        state: HistoricalState,
        evidence_resolver: EvidenceResolver,
        rerun_executor: HistoricalExecutor,
        regrade_executor: HistoricalExecutor,
        replay_executor: HistoricalExecutor,
        claim_stale_after_s: int = 24 * 60 * 60,
    ) -> None:
        if (
            isinstance(claim_stale_after_s, bool)
            or not isinstance(claim_stale_after_s, int)
            or claim_stale_after_s <= 0
        ):
            raise ValueError("claim_stale_after_s must be a positive integer")
        self._state = state
        self._evidence_resolver = evidence_resolver
        self._executors = {
            HistoricalOperation.RERUN: rerun_executor,
            HistoricalOperation.REGRADE: regrade_executor,
            HistoricalOperation.REPLAY: replay_executor,
        }
        self._claim_stale_after_s = claim_stale_after_s
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._locks_guard = threading.Lock()

    async def rerun(
        self,
        request: HistoricalOperationRequest,
    ) -> HistoricalOperationReceipt:
        return await self.execute(_require_operation(request, HistoricalOperation.RERUN))

    async def regrade(
        self,
        request: HistoricalOperationRequest,
    ) -> HistoricalOperationReceipt:
        return await self.execute(_require_operation(request, HistoricalOperation.REGRADE))

    async def replay(
        self,
        request: HistoricalOperationRequest,
    ) -> HistoricalOperationReceipt:
        return await self.execute(_require_operation(request, HistoricalOperation.REPLAY))

    async def execute(
        self,
        request: HistoricalOperationRequest,
    ) -> HistoricalOperationReceipt:
        self._require_authoritative_state()
        lock = self._acquire_operation_lock(request.operation_id)
        try:
            async with lock:
                return await self._execute_once(request)
        finally:
            self._release_operation_lock(request.operation_id)

    async def _execute_once(
        self,
        request: HistoricalOperationRequest,
    ) -> HistoricalOperationReceipt:
        claim, reserved = self._state.reserve_historical_operation(
            operation_id=request.operation_id,
            request_hash=request.request_hash,
            operation=request.operation.value,
        )
        self._validate_claim(request, claim)
        existing = self._existing_terminal_receipt(request)
        if existing is not None:
            self._state.complete_historical_operation(
                operation_id=request.operation_id,
                request_hash=request.request_hash,
                status="completed",
                terminal_event_id=existing.completed_event_id,
            )
            self._verify_receipt_evidence(request, existing)
            self._verify_terminal_checkpoint(
                operation_id=request.operation_id,
                event_id=existing.completed_event_id,
                event_kind="historical_operation.completed",
            )
            return existing
        if not reserved:
            status = str(claim.get("status") or "")
            if status == "failed":
                raise HistoricalEvidenceError(
                    "historical operation claim is failed but its failure "
                    "event is missing"
                )
            if status == "completed":
                raise HistoricalEvidenceError(
                    "historical operation claim is completed but its "
                    "completion event is missing"
                )
            if self._claim_is_stale(claim):
                raise HistoricalOperationIndeterminate(
                    "historical operation claim is stale and may have "
                    "crossed the side-effect boundary; automatic "
                    "re-execution is forbidden"
                )
            raise HistoricalOperationInProgress(
                "historical operation is already running in another process"
            )
        requested_event_id = self._state.write_historical_operation_event(
            run_id=request.operation_id,
            kind="historical_operation.requested",
            payload={
                "operation_id": request.operation_id,
                "request_hash": request.request_hash,
                "request": request.to_dict(),
            },
        )

        try:
            self._verify_source(request.source)
            raw_result = self._executors[request.operation](request)
            if inspect.isawaitable(raw_result):
                raw_result = await raw_result
            result = _redact_result_mapping(raw_result)
            self._validate_result(request, result)
            result_ref = _required_text(
                "result.artifact_ref",
                result.get("artifact_ref"),
            )
            result_sha256 = _required_sha256(
                "result.artifact_sha256",
                result.get("artifact_sha256"),
            )
            self._verify_evidence(
                ref=result_ref,
                expected_sha256=result_sha256,
                label="historical result artifact",
            )
            if request.operation is HistoricalOperation.RERUN:
                self._verify_rerun_manifest(request, result)
            self._verify_source(request.source)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed_event_id = (
                self._state.write_historical_operation_event(
                run_id=request.operation_id,
                kind="historical_operation.failed",
                payload={
                    "operation_id": request.operation_id,
                    "request_hash": request.request_hash,
                    "operation": request.operation.value,
                    "requested_event_id": requested_event_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                )
            )
            self._state.complete_historical_operation(
                operation_id=request.operation_id,
                request_hash=request.request_hash,
                status="failed",
                terminal_event_id=failed_event_id,
            )
            self._verify_terminal_checkpoint(
                operation_id=request.operation_id,
                event_id=failed_event_id,
                event_kind="historical_operation.failed",
            )
            raise

        receipt_body = {
            "schema_version": HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION,
            "operation_id": request.operation_id,
            "operation": request.operation.value,
            "request_hash": request.request_hash,
            "source_run_id": request.source.source_run_id,
            "task_id": request.source.task_id,
            "result_ref": result_ref,
            "result_sha256": result_sha256,
            "result": result,
            "requested_event_id": requested_event_id,
        }
        receipt_hash = _sha256_json(receipt_body)
        completed_event_id = (
            self._state.write_historical_operation_event(
            run_id=request.operation_id,
            kind="historical_operation.completed",
            payload={
                **receipt_body,
                "receipt_hash": receipt_hash,
            },
            )
        )
        self._state.complete_historical_operation(
            operation_id=request.operation_id,
            request_hash=request.request_hash,
            status="completed",
            terminal_event_id=completed_event_id,
        )
        self._verify_terminal_checkpoint(
            operation_id=request.operation_id,
            event_id=completed_event_id,
            event_kind="historical_operation.completed",
        )
        return HistoricalOperationReceipt(
            operation_id=request.operation_id,
            operation=request.operation,
            request_hash=request.request_hash,
            source_run_id=request.source.source_run_id,
            task_id=request.source.task_id,
            result_ref=result_ref,
            result_sha256=result_sha256,
            result=result,
            requested_event_id=requested_event_id,
            completed_event_id=completed_event_id,
            receipt_hash=receipt_hash,
        )

    def _acquire_operation_lock(self, operation_id: str) -> asyncio.Lock:
        with self._locks_guard:
            entry = self._locks.get(operation_id)
            lock = entry[0] if entry is not None else asyncio.Lock()
            holders = entry[1] if entry is not None else 0
            self._locks[operation_id] = (lock, holders + 1)
            return lock

    def _release_operation_lock(self, operation_id: str) -> None:
        with self._locks_guard:
            lock, holders = self._locks[operation_id]
            if holders <= 1:
                del self._locks[operation_id]
            else:
                self._locks[operation_id] = (lock, holders - 1)

    def _claim_is_stale(self, claim: Mapping[str, Any]) -> bool:
        try:
            updated_at = int(claim.get("updated_at"))
        except (TypeError, ValueError):
            return True
        return int(time.time()) - updated_at >= self._claim_stale_after_s

    def _require_authoritative_state(self) -> None:
        if (
            str(getattr(self._state, "event_ledger_assurance", ""))
            != "authoritative"
        ):
            raise HistoricalEvidenceError(
                "historical evaluation requires authoritative "
                "event-ledger assurance"
            )

    def _verify_terminal_checkpoint(
        self,
        *,
        operation_id: str,
        event_id: int,
        event_kind: str,
    ) -> None:
        self._state.ensure_event_checkpoint(
            run_id=operation_id,
            event_id=event_id,
            event_kind=event_kind,
        )
        verification = self._state.verify_event_ledger(operation_id)
        if (
            getattr(verification, "valid", False) is not True
            or getattr(
                verification,
                "authoritative_head_verified",
                False,
            )
            is not True
            or getattr(verification, "head_event_id", None) != event_id
        ):
            failure_code = str(
                getattr(verification, "failure_code", "") or "unknown"
            )
            raise HistoricalEvidenceError(
                "authoritative checkpoint does not cover terminal event "
                f"{event_id}: {failure_code}"
            )

    def _verify_source(self, source: HistoricalSource) -> None:
        self._verify_evidence(
            ref=source.source_manifest_ref,
            expected_sha256=source.source_manifest_sha256,
            label="source run manifest",
        )
        self._verify_evidence(
            ref=source.frozen_result_ref,
            expected_sha256=source.frozen_result_sha256,
            label="source frozen result",
        )

    def _verify_receipt_evidence(
        self,
        request: HistoricalOperationRequest,
        receipt: HistoricalOperationReceipt,
    ) -> None:
        if (
            receipt.operation_id != request.operation_id
            or receipt.operation is not request.operation
            or receipt.request_hash != request.request_hash
            or receipt.source_run_id != request.source.source_run_id
            or receipt.task_id != request.source.task_id
        ):
            raise HistoricalEvidenceError(
                "historical completion receipt does not match its request"
            )
        self._verify_source(request.source)
        self._validate_result(request, receipt.result)
        self._verify_evidence(
            ref=receipt.result_ref,
            expected_sha256=receipt.result_sha256,
            label="historical result artifact",
        )
        if request.operation is HistoricalOperation.RERUN:
            self._verify_rerun_manifest(request, receipt.result)

    def _verify_rerun_manifest(
        self,
        request: HistoricalOperationRequest,
        result: Mapping[str, Any],
    ) -> None:
        manifest_bytes = self._verify_evidence(
            ref=_required_text(
                "result.run_manifest_ref",
                result.get("run_manifest_ref"),
            ),
            expected_sha256=_required_sha256(
                "result.run_manifest_sha256",
                result.get("run_manifest_sha256"),
            ),
            label="rerun manifest",
        )
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HistoricalEvidenceError(
                "rerun manifest is not a UTF-8 JSON object"
            ) from exc
        if not isinstance(manifest, dict):
            raise HistoricalEvidenceError(
                "rerun manifest is not a UTF-8 JSON object"
            )
        expected_identity = {
            "run_id": _required_text(
                "result.new_run_id",
                result.get("new_run_id"),
            ),
            "source_run_id": request.source.source_run_id,
            "task_id": request.source.task_id,
        }
        for field_name, expected in expected_identity.items():
            observed = _required_text(
                f"rerun manifest {field_name}",
                manifest.get(field_name),
            )
            if observed != expected:
                if field_name == "run_id":
                    raise HistoricalSemanticsError(
                        "rerun manifest run_id does not match new_run_id"
                    )
                raise HistoricalSemanticsError(
                    f"rerun manifest {field_name} does not match its source"
                )

    def _verify_evidence(
        self,
        *,
        ref: str,
        expected_sha256: str,
        label: str,
    ) -> bytes:
        try:
            value = self._evidence_resolver(ref)
        except Exception as exc:
            raise HistoricalEvidenceError(
                f"{label} could not be resolved: {ref}"
            ) from exc
        if isinstance(value, bytes):
            resolved = value
        elif isinstance(value, (bytearray, memoryview)):
            resolved = bytes(value)
        else:
            resolved = None
        if (
            resolved is None
            or sha256(resolved).hexdigest() != expected_sha256
        ):
            raise HistoricalEvidenceError(
                f"{label} hash mismatch or missing: {ref}"
            )
        return resolved

    def _existing_terminal_receipt(
        self,
        request: HistoricalOperationRequest,
    ) -> HistoricalOperationReceipt | None:
        after = 0
        requested_events: list[Mapping[str, Any]] = []
        terminal_events: list[Mapping[str, Any]] = []
        while True:
            page = self._state.read_events_since(
                request.operation_id,
                after_event_id=after,
                limit=200,
            )
            if not page:
                break
            page_floor = after
            for event in page:
                after = max(after, int(event.get("event_id") or 0))
                kind = str(event.get("kind") or "")
                if not kind.startswith("historical_operation."):
                    continue
                if (
                    str(event.get("source") or "")
                    != HISTORICAL_OPERATION_EVENT_SOURCE
                ):
                    raise HistoricalEvidenceError(
                        "historical operation event has an unauthorized source"
                    )
                payload = event.get("payload")
                if not isinstance(payload, Mapping):
                    raise HistoricalEvidenceError(
                        "historical operation event payload is malformed"
                    )
                observed_hash = str(payload.get("request_hash") or "")
                if observed_hash and observed_hash != request.request_hash:
                    raise HistoricalIdempotencyConflict(
                        "historical operation idempotency key was reused "
                        "for different request content"
                    )
                if kind == "historical_operation.requested":
                    if (
                        str(payload.get("operation_id") or "")
                        != request.operation_id
                        or observed_hash != request.request_hash
                        or _normalise_json(payload.get("request"))
                        != request.to_dict()
                    ):
                        raise HistoricalEvidenceError(
                            "historical requested event does not match its "
                            "request"
                        )
                    requested_events.append(event)
                elif kind in {
                    "historical_operation.failed",
                    "historical_operation.completed",
                }:
                    if (
                        str(payload.get("operation_id") or "")
                        != request.operation_id
                        or str(payload.get("operation") or "")
                        != request.operation.value
                        or observed_hash != request.request_hash
                    ):
                        raise HistoricalEvidenceError(
                            "historical terminal event identity does not "
                            "match its request"
                        )
                    terminal_events.append(event)
                else:
                    raise HistoricalEvidenceError(
                        f"unsupported historical operation event kind: {kind}"
                    )
            if after <= page_floor:
                raise HistoricalEvidenceError(
                    "historical operation event pagination did not advance "
                    "past event ids already read"
                )
            if len(page) < 200:
                break
        if len(requested_events) > 1:
            raise HistoricalEvidenceError(
                "historical operation has multiple requested events"
            )
        if len(terminal_events) > 1:
            raise HistoricalEvidenceError(
                "historical operation has multiple terminal events"
            )
        if not terminal_events:
            return None
        if len(requested_events) != 1:
            raise HistoricalEvidenceError(
                "historical terminal event has no unique requested event"
            )
        requested_event_id = _required_positive_int(
            "requested_event_id",
            requested_events[0].get("event_id"),
        )
        terminal = terminal_events[0]
        terminal_payload = terminal["payload"]
        linked_requested_event_id = _required_positive_int(
            "terminal.requested_event_id",
            terminal_payload.get("requested_event_id"),
        )
        if linked_requested_event_id != requested_event_id:
            raise HistoricalEvidenceError(
                "historical terminal event requested-event linkage is invalid"
            )
        event_id = _required_positive_int(
            "terminal.event_id",
            terminal.get("event_id"),
        )
        if event_id <= requested_event_id:
            raise HistoricalEvidenceError(
                "historical terminal event precedes its requested event"
            )
        kind = str(terminal.get("kind") or "")
        if kind == "historical_operation.failed":
            self._state.complete_historical_operation(
                operation_id=request.operation_id,
                request_hash=request.request_hash,
                status="failed",
                terminal_event_id=event_id,
            )
            self._verify_terminal_checkpoint(
                operation_id=request.operation_id,
                event_id=event_id,
                event_kind=kind,
            )
            raise HistoricalOperationPreviouslyFailed(
                "historical operation already failed; use a new "
                "idempotency key after correcting the cause"
            )
        return _receipt_from_event(terminal)

    @staticmethod
    def _validate_claim(
        request: HistoricalOperationRequest,
        claim: Mapping[str, Any],
    ) -> None:
        if (
            str(claim.get("operation_id") or "") != request.operation_id
            or str(claim.get("request_hash") or "") != request.request_hash
            or str(claim.get("operation") or "") != request.operation.value
        ):
            raise HistoricalIdempotencyConflict(
                "historical operation idempotency key was reused for "
                "different request content"
            )

    @staticmethod
    def _validate_result(
        request: HistoricalOperationRequest,
        result: Mapping[str, Any],
    ) -> None:
        if result.get("status") != "completed":
            raise HistoricalSemanticsError(
                "historical operation result status must be completed"
            )
        operation = request.operation
        execution_performed = result.get("execution_performed")
        if operation is HistoricalOperation.RERUN:
            if execution_performed is not True:
                raise HistoricalSemanticsError(
                    "rerun must perform a new execution"
                )
            new_run_id = _required_text(
                "result.new_run_id",
                result.get("new_run_id"),
            )
            if new_run_id == request.source.source_run_id:
                raise HistoricalSemanticsError(
                    "rerun must create a new run identity"
                )
            _required_sha256(
                "result.run_manifest_sha256",
                result.get("run_manifest_sha256"),
            )
            _required_text(
                "result.run_manifest_ref",
                result.get("run_manifest_ref"),
            )
            return

        if execution_performed is not False:
            raise HistoricalSemanticsError(
                f"{operation.value} must not execute an agent"
            )
        source_result_hash = _required_sha256(
            "result.source_frozen_result_sha256",
            result.get("source_frozen_result_sha256"),
        )
        if source_result_hash != request.source.frozen_result_sha256:
            raise HistoricalSemanticsError(
                f"{operation.value} changed the frozen source result"
            )

        if operation is HistoricalOperation.REGRADE:
            _required_text(
                "result.grade_revision_id",
                result.get("grade_revision_id"),
            )
            _required_text(
                "result.verifier_id",
                result.get("verifier_id"),
            )
            _required_text(
                "result.verifier_version",
                result.get("verifier_version"),
            )
            for field_name in (
                "verifier_config_sha256",
                "verifier_implementation_sha256",
            ):
                _required_sha256(
                    f"result.{field_name}",
                    result.get(field_name),
                )
            if request.source.current_grade_id is not None:
                supersedes = _required_text(
                    "result.supersedes_grade_id",
                    result.get("supersedes_grade_id"),
                )
                if supersedes != request.source.current_grade_id:
                    raise HistoricalSemanticsError(
                        "regrade must supersede the source grade revision"
                    )
            return

        if result.get("deterministic") is not True:
            raise HistoricalSemanticsError(
                "replay must declare deterministic recomputation"
            )
        source_manifest_hash = _required_sha256(
            "result.source_manifest_sha256",
            result.get("source_manifest_sha256"),
        )
        if source_manifest_hash != request.source.source_manifest_sha256:
            raise HistoricalSemanticsError(
                "replay used a different source manifest"
            )
        _required_text(
            "result.replay_schema_version",
            result.get("replay_schema_version"),
        )


def _require_operation(
    request: HistoricalOperationRequest,
    expected: HistoricalOperation,
) -> HistoricalOperationRequest:
    if request.operation is not expected:
        raise HistoricalSemanticsError(
            f"expected {expected.value} request, got {request.operation.value}"
        )
    return request


def _receipt_from_event(
    event: Mapping[str, Any],
) -> HistoricalOperationReceipt:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise HistoricalEvidenceError(
            "historical completion event payload is malformed"
        )
    receipt_body = {
        key: payload.get(key)
        for key in (
            "schema_version",
            "operation_id",
            "operation",
            "request_hash",
            "source_run_id",
            "task_id",
            "result_ref",
            "result_sha256",
            "result",
            "requested_event_id",
        )
    }
    expected_hash = _sha256_json(receipt_body)
    observed_hash = _required_sha256(
        "receipt_hash",
        payload.get("receipt_hash"),
    )
    if expected_hash != observed_hash:
        raise HistoricalEvidenceError(
            "historical completion receipt hash does not verify"
        )
    return HistoricalOperationReceipt(
        schema_version=_require_schema_version(
            field_name="receipt.schema_version",
            observed=payload.get("schema_version"),
            expected=HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION,
            label="historical operation receipt",
        ),
        operation_id=_required_text(
            "operation_id",
            payload.get("operation_id"),
        ),
        operation=HistoricalOperation(str(payload.get("operation"))),
        request_hash=_required_sha256(
            "request_hash",
            payload.get("request_hash"),
        ),
        source_run_id=_required_text(
            "source_run_id",
            payload.get("source_run_id"),
        ),
        task_id=_required_text("task_id", payload.get("task_id")),
        result_ref=_required_text(
            "result_ref",
            payload.get("result_ref"),
        ),
        result_sha256=_required_sha256(
            "result_sha256",
            payload.get("result_sha256"),
        ),
        result=_stored_result_mapping(payload.get("result")),
        requested_event_id=int(payload.get("requested_event_id") or 0),
        completed_event_id=int(event.get("event_id") or 0),
        receipt_hash=observed_hash,
    )


def _required_text(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise HistoricalSemanticsError(f"{field_name} is required")
    return text


def _required_sha256(field_name: str, value: Any) -> str:
    digest = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise HistoricalSemanticsError(
            f"{field_name} must be a canonical SHA-256"
        )
    return digest


def _redact_result_mapping(value: Any) -> dict[str, Any]:
    normalized = _normalise_json_mapping(value, field="result")
    safe = _normalise_json_mapping(redact(normalized), field="result")
    changed_identity_fields = sorted(
        field_name
        for field_name in _RESULT_IDENTITY_FIELDS
        if normalized.get(field_name) != safe.get(field_name)
    )
    if changed_identity_fields:
        raise HistoricalSemanticsError(
            "historical result identity/reference fields may not contain "
            "secret-shaped values: "
            + ", ".join(changed_identity_fields)
        )
    return safe


def _stored_result_mapping(value: Any) -> dict[str, Any]:
    normalized = _normalise_json_mapping(value, field="result")
    safe = _normalise_json_mapping(redact(normalized), field="result")
    if safe != normalized:
        raise HistoricalEvidenceError(
            "historical completion receipt contains unredacted result data"
        )
    return normalized


def _required_positive_int(field_name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise HistoricalSemanticsError(
            f"{field_name} must be a positive integer"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalSemanticsError(
            f"{field_name} must be a positive integer"
        ) from exc
    if parsed <= 0:
        raise HistoricalSemanticsError(
            f"{field_name} must be a positive integer"
        )
    return parsed


def _require_schema_version(
    *,
    field_name: str,
    observed: Any,
    expected: str,
    label: str,
) -> str:
    version = _required_text(field_name, observed)
    if version != expected:
        raise HistoricalSemanticsError(
            f"unsupported {label} schema: {version}"
        )
    return version


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _deep_freeze(nested)
                for key, nested in value.items()
            }
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray, memoryview),
    ):
        return tuple(_deep_freeze(nested) for nested in value)
    return value


def _normalise_json_mapping(
    value: Any,
    *,
    field: str,
) -> dict[str, Any]:
    normalized = _normalise_json(value)
    if not isinstance(normalized, dict):
        raise HistoricalSemanticsError(f"{field} must be a mapping")
    return normalized


def _normalise_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        value = {
            str(key): _normalise_json(nested)
            for key, nested in value.items()
        }
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray, memoryview),
    ):
        value = [_normalise_json(nested) for nested in value]
    try:
        return json.loads(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise HistoricalSemanticsError(
            "historical operation payload must be canonical JSON"
        ) from exc


def _sha256_json(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "EvidenceResolver",
    "HISTORICAL_OPERATION_EVENT_SOURCE",
    "HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION",
    "HISTORICAL_OPERATION_REQUEST_SCHEMA_VERSION",
    "HistoricalEvaluationError",
    "HistoricalEvaluationService",
    "HistoricalEvidenceError",
    "HistoricalIdempotencyConflict",
    "HistoricalOperation",
    "HistoricalOperationIndeterminate",
    "HistoricalOperationInProgress",
    "HistoricalOperationPreviouslyFailed",
    "HistoricalOperationReceipt",
    "HistoricalOperationRequest",
    "HistoricalSemanticsError",
    "HistoricalSource",
]
