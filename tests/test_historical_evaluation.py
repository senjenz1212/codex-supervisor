from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from supervisor.evidence_committer import HmacCheckpointAuthority
from supervisor.historical_evaluation import (
    HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION,
    HistoricalEvaluationService,
    HistoricalEvidenceError,
    HistoricalIdempotencyConflict,
    HistoricalOperation,
    HistoricalOperationIndeterminate,
    HistoricalOperationInProgress,
    HistoricalOperationPreviouslyFailed,
    HistoricalOperationReceipt,
    RECEIPT_REDACTION_RULES_VERSION,
    HistoricalOperationRequest,
    HistoricalSemanticsError,
    HistoricalSource,
)
from supervisor.ledger_checkpoints import (
    FilesystemTrustedCheckpointPinStore,
    LedgerCheckpointCoordinator,
    LedgerCheckpointPolicy,
    LedgerCheckpointStore,
)
from supervisor.state import State


def _write(root: Path, name: str, value: bytes) -> tuple[str, str]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return str(path), hashlib.sha256(value).hexdigest()


def _fixture(tmp_path: Path):
    manifest_ref, manifest_hash = _write(
        tmp_path,
        "source/manifest.json",
        b'{"run":"source-run"}',
    )
    result_ref, result_hash = _write(
        tmp_path,
        "source/frozen-result.json",
        b'{"patch":"abc"}',
    )
    source = HistoricalSource(
        source_run_id="source-run",
        task_id="task-1",
        source_manifest_ref=manifest_ref,
        source_manifest_sha256=manifest_hash,
        frozen_result_ref=result_ref,
        frozen_result_sha256=result_hash,
        current_grade_id="grade-1",
    )
    artifacts: dict[str, bytes] = {
        manifest_ref: Path(manifest_ref).read_bytes(),
        result_ref: Path(result_ref).read_bytes(),
    }
    calls = {"rerun": 0, "regrade": 0, "replay": 0}

    def artifact(name: str, payload: bytes) -> tuple[str, str]:
        ref, digest = _write(tmp_path, f"outputs/{name}.json", payload)
        artifacts[ref] = payload
        return ref, digest

    async def rerun(_request):
        calls["rerun"] += 1
        ref, digest = artifact("rerun", b'{"status":"passed"}')
        manifest_ref, manifest_digest = artifact(
            "rerun-manifest",
            (
                b'{"run_id":"rerun-2","source_run_id":"source-run",'
                b'"task_id":"task-1"}'
            ),
        )
        return {
            "status": "completed",
            "execution_performed": True,
            "new_run_id": "rerun-2",
            "run_manifest_ref": manifest_ref,
            "run_manifest_sha256": manifest_digest,
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    def regrade(request):
        calls["regrade"] += 1
        ref, digest = artifact("regrade", b'{"score":1}')
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": (
                request.source.frozen_result_sha256
            ),
            "grade_revision_id": "grade-2",
            "supersedes_grade_id": "grade-1",
            "verifier_id": "hidden-tests",
            "verifier_version": "2",
            "verifier_config_sha256": "b" * 64,
            "verifier_implementation_sha256": "c" * 64,
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    def replay(request):
        calls["replay"] += 1
        ref, digest = artifact("replay", b'{"decision":"same"}')
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": (
                request.source.frozen_result_sha256
            ),
            "source_manifest_sha256": (
                request.source.source_manifest_sha256
            ),
            "deterministic": True,
            "replay_schema_version": "replay/v1",
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    authority = HmacCheckpointAuthority(
        key_id="historical-test-key",
        key=b"historical-test-checkpoint-key",
    )
    coordinator = LedgerCheckpointCoordinator(
        signer=authority,
        verifier=authority,
        checkpoint_store=LedgerCheckpointStore(
            tmp_path / "external-checkpoints"
        ),
        trusted_pin_store=FilesystemTrustedCheckpointPinStore(
            tmp_path / "trusted-checkpoint-pins"
        ),
        policy=LedgerCheckpointPolicy(max_events_between_checkpoints=100),
    )
    state = State(
        str(tmp_path / "state.db"),
        ledger_checkpoint_coordinator=coordinator,
    )
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=rerun,
        regrade_executor=regrade,
        replay_executor=replay,
    )
    return state, service, source, artifacts, calls


def _request(
    operation: HistoricalOperation,
    source: HistoricalSource,
    *,
    key: str,
    options: dict | None = None,
) -> HistoricalOperationRequest:
    return HistoricalOperationRequest(
        operation=operation,
        source=source,
        idempotency_key=key,
        requested_by="operator",
        reason="audit historical evidence",
        options=options or {},
    )


def test_request_and_receipt_reject_unknown_schemas_and_freeze_nested_identity(
    tmp_path,
):
    _state, _service, source, _artifacts, _calls = _fixture(tmp_path)
    options = {
        "nested": {
            "items": ["one"],
            "authorization": "Bearer request-secret-123",
        }
    }

    with pytest.raises(
        HistoricalSemanticsError,
        match="unsupported historical operation request schema",
    ):
        HistoricalOperationRequest(
            operation=HistoricalOperation.REPLAY,
            source=source,
            idempotency_key="future-request",
            requested_by="operator",
            reason="future schema",
            options={},
            schema_version="supervisor-historical-operation-request/v99",
        )

    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="immutable-request",
        options=options,
    )
    request_hash = request.request_hash
    options["nested"]["items"].append("mutated")

    assert request.request_hash == request_hash
    assert request.to_dict()["options"] == {
        "nested": {
            "items": ["one"],
            "authorization": "Bearer [REDACTED_BEARER]",
        },
    }
    assert "request-secret-123" not in str(request.to_dict())
    with pytest.raises(TypeError):
        request.options["nested"]["other"] = True
    with pytest.raises(AttributeError):
        request.options["nested"]["items"].append("two")

    with pytest.raises(
        HistoricalSemanticsError,
        match="unsupported historical operation receipt schema",
    ):
        HistoricalOperationReceipt(
            operation_id=request.operation_id,
            operation=request.operation,
            request_hash=request.request_hash,
            source_run_id=source.source_run_id,
            task_id=source.task_id,
            result_ref="artifact://result",
            result_sha256="a" * 64,
            result={"nested": {"items": ["one"]}},
            requested_event_id=1,
            completed_event_id=2,
            receipt_hash="b" * 64,
            schema_version=(
                HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION + "-future"
            ),
        )


@pytest.mark.asyncio
async def test_future_receipt_schema_fails_closed_without_reexecution(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="future-receipt-schema",
    )
    state.reserve_historical_operation(
        operation_id=request.operation_id,
        request_hash=request.request_hash,
        operation=request.operation.value,
    )
    requested_event_id = state.write_historical_operation_event(
        run_id=request.operation_id,
        kind="historical_operation.requested",
        payload={
            "operation_id": request.operation_id,
            "request_hash": request.request_hash,
            "request": request.to_dict(),
        },
    )
    result_ref, result_sha256 = _write(
        tmp_path,
        "outputs/future-schema-result.json",
        b'{"decision":"same"}',
    )
    artifacts[result_ref] = Path(result_ref).read_bytes()
    result = {
        "status": "completed",
        "execution_performed": False,
        "source_frozen_result_sha256": source.frozen_result_sha256,
        "source_manifest_sha256": source.source_manifest_sha256,
        "deterministic": True,
        "replay_schema_version": "replay/v1",
        "artifact_ref": result_ref,
        "artifact_sha256": result_sha256,
    }
    receipt_body = {
        "schema_version": "supervisor-historical-operation-receipt/v99",
        "operation_id": request.operation_id,
        "operation": request.operation.value,
        "request_hash": request.request_hash,
        "source_run_id": source.source_run_id,
        "task_id": source.task_id,
        "result_ref": result_ref,
        "result_sha256": result_sha256,
        "result": result,
        "requested_event_id": requested_event_id,
    }
    receipt_hash = hashlib.sha256(
        json.dumps(
            receipt_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    state.write_historical_operation_event(
        run_id=request.operation_id,
        kind="historical_operation.completed",
        payload={**receipt_body, "receipt_hash": receipt_hash},
    )
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: pytest.fail(
            "future receipt schema must never re-execute"
        ),
    )

    with pytest.raises(
        HistoricalSemanticsError,
        match="unsupported historical operation receipt schema",
    ):
        await service.replay(request)


@pytest.mark.asyncio
async def test_unified_service_preserves_distinct_operation_semantics(
    tmp_path,
):
    state, service, source, _artifacts, calls = _fixture(tmp_path)

    rerun = await service.rerun(
        _request(HistoricalOperation.RERUN, source, key="rerun-1")
    )
    regrade = await service.regrade(
        _request(HistoricalOperation.REGRADE, source, key="regrade-1")
    )
    replay = await service.replay(
        _request(HistoricalOperation.REPLAY, source, key="replay-1")
    )

    assert rerun.result["new_run_id"] == "rerun-2"
    assert regrade.result["supersedes_grade_id"] == "grade-1"
    assert replay.result["deterministic"] is True
    assert calls == {"rerun": 1, "regrade": 1, "replay": 1}
    for receipt in (rerun, regrade, replay):
        events = state.read_events_since(
            receipt.operation_id,
            after_event_id=0,
            limit=10,
        )
        assert [event["kind"] for event in events] == [
            "historical_operation.requested",
            "historical_operation.completed",
        ]
        verification = state.verify_event_ledger_structure(
            receipt.operation_id
        )
        assert verification.valid is True
        authoritative = state.verify_event_ledger(receipt.operation_id)
        assert authoritative.valid is True
        assert authoritative.authoritative_head_verified is True
        assert authoritative.head_event_id == receipt.completed_event_id


@pytest.mark.asyncio
async def test_service_rejects_diagnostic_only_state_before_execution(tmp_path):
    _authoritative, _service, source, artifacts, _calls = _fixture(tmp_path)
    diagnostic = State(str(tmp_path / "diagnostic.db"))
    calls = 0

    def replay(_request):
        nonlocal calls
        calls += 1
        return {}

    service = HistoricalEvaluationService(
        state=diagnostic,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=replay,
    )

    with pytest.raises(
        HistoricalEvidenceError,
        match="requires authoritative event-ledger assurance",
    ):
        await service.replay(
            _request(
                HistoricalOperation.REPLAY,
                source,
                key="diagnostic-state-rejected",
            )
        )

    assert calls == 0


@pytest.mark.asyncio
async def test_success_is_not_returned_without_terminal_checkpoint_coverage(
    tmp_path,
    monkeypatch,
):
    state, service, source, _artifacts, calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="checkpoint-coverage-required",
    )
    monkeypatch.setattr(
        state,
        "verify_event_ledger",
        lambda _run_id: SimpleNamespace(
            valid=False,
            authoritative_head_verified=False,
            head_event_id=None,
            failure_code="trusted_head_required",
        ),
    )

    with pytest.raises(
        HistoricalEvidenceError,
        match="authoritative checkpoint does not cover terminal event",
    ):
        await service.replay(request)

    assert calls["replay"] == 1


@pytest.mark.asyncio
async def test_same_request_is_idempotent_without_reexecution(tmp_path):
    _state, service, source, _artifacts, calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="same-replay",
    )

    first = await service.execute(request)
    second = await service.execute(request)

    assert second.to_dict() == first.to_dict()
    assert calls["replay"] == 1


@pytest.mark.asyncio
async def test_result_is_redacted_before_hash_storage_and_response(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    calls = 0

    def replay_with_secret(request):
        nonlocal calls
        calls += 1
        ref, digest = _write(
            tmp_path,
            "outputs/redacted-replay.json",
            b'{"decision":"same"}',
        )
        artifacts[ref] = Path(ref).read_bytes()
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": (
                request.source.frozen_result_sha256
            ),
            "source_manifest_sha256": (
                request.source.source_manifest_sha256
            ),
            "deterministic": True,
            "replay_schema_version": "replay/v1",
            "artifact_ref": ref,
            "artifact_sha256": digest,
            "diagnostics": {
                "authorization": "Bearer secret-token-123",
            },
        }

    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="redacted-replay",
    )
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=replay_with_secret,
    )

    first = await service.replay(request)
    assert first.result["diagnostics"]["authorization"] == (
        "Bearer [REDACTED_BEARER]"
    )
    assert "secret-token-123" not in str(first.to_dict())
    with pytest.raises(TypeError):
        first.result["diagnostics"]["extra"] = "mutated"
    assert first.receipt_hash == first.to_dict()["receipt_hash"]

    second = await HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: pytest.fail(
            "idempotent retry must not re-execute"
        ),
    ).replay(request)

    assert second.to_dict() == first.to_dict()
    assert calls == 1
    stored = str(
        state.read_events_since(
            request.operation_id,
            after_event_id=0,
            limit=10,
        )
    )
    assert "secret-token-123" not in stored


@pytest.mark.asyncio
async def test_concurrent_same_request_executes_once(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    calls = 0

    async def slow_replay(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        ref, digest = _write(
            tmp_path,
            "outputs/concurrent-replay.json",
            b'{"decision":"same"}',
        )
        artifacts[ref] = Path(ref).read_bytes()
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": (
                request.source.frozen_result_sha256
            ),
            "source_manifest_sha256": (
                request.source.source_manifest_sha256
            ),
            "deterministic": True,
            "replay_schema_version": "replay/v1",
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=slow_replay,
    )
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="concurrent-replay",
    )

    first, second = await asyncio.gather(
        service.replay(request),
        service.replay(request),
    )

    assert calls == 1
    assert first.to_dict() == second.to_dict()


@pytest.mark.asyncio
async def test_two_service_instances_fail_closed_while_same_request_runs(
    tmp_path,
):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_replay(request):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        ref, digest = _write(
            tmp_path,
            "outputs/cross-service-replay.json",
            b'{"decision":"same"}',
        )
        artifacts[ref] = Path(ref).read_bytes()
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": (
                request.source.frozen_result_sha256
            ),
            "source_manifest_sha256": (
                request.source.source_manifest_sha256
            ),
            "deterministic": True,
            "replay_schema_version": "replay/v1",
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    second_state = State(
        state.db_path,
        ledger_checkpoint_coordinator=(
            state._ledger_checkpoint_coordinator
        ),
    )

    def build_service(bound_state):
        return HistoricalEvaluationService(
            state=bound_state,
            evidence_resolver=artifacts.get,
            rerun_executor=lambda _request: {},
            regrade_executor=lambda _request: {},
            replay_executor=slow_replay,
        )

    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="cross-service-replay",
    )
    first_task = asyncio.create_task(build_service(state).replay(request))
    await started.wait()
    try:
        with pytest.raises(HistoricalOperationInProgress):
            await build_service(second_state).replay(request)
    finally:
        release.set()
    await first_task

    assert calls == 1


@pytest.mark.parametrize(
    ("retry_state", "expected_events"),
    [
        (
            "stale",
            [
                "historical_operation.requested",
                "historical_operation.completed",
            ],
        ),
        (
            "preflight_released",
            [
                "historical_operation.preflight_released",
                "historical_operation.requested",
                "historical_operation.completed",
            ],
        ),
    ],
)
def test_two_sqlite_service_instances_claim_retry_execution_once(
    tmp_path,
    retry_state,
    expected_events,
):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    second_state = State(
        state.db_path,
        ledger_checkpoint_coordinator=(
            state._ledger_checkpoint_coordinator
        ),
    )
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="cross-service-stale-retry",
    )
    state.reserve_historical_operation(
        operation_id=request.operation_id,
        request_hash=request.request_hash,
        operation=request.operation.value,
    )
    if retry_state == "stale":
        _mark_claim_stale(state, request.operation_id)
    else:
        state.write_historical_operation_event(
            run_id=request.operation_id,
            kind="historical_operation.preflight_released",
            payload={
                "operation_id": request.operation_id,
                "request_hash": request.request_hash,
                "operation": request.operation.value,
                "error_type": "HistoricalEvidenceError",
                "error": "retryable preflight failure",
            },
        )

    result_ref, result_sha256 = _write(
        tmp_path,
        "outputs/cross-service-stale-retry.json",
        b'{"decision":"same"}',
    )
    artifacts[result_ref] = Path(result_ref).read_bytes()
    preflight_barrier = threading.Barrier(2)
    preflight_seen = threading.local()
    executor_release = threading.Event()
    calls_changed = threading.Condition()
    calls = 0

    def resolver(ref):
        if (
            ref == source.source_manifest_ref
            and not getattr(preflight_seen, "synchronized", False)
        ):
            preflight_seen.synchronized = True
            preflight_barrier.wait(timeout=5)
        return artifacts.get(ref)

    def replay(retry_request):
        nonlocal calls
        with calls_changed:
            calls += 1
            calls_changed.notify_all()
        if not executor_release.wait(timeout=5):
            raise TimeoutError("test did not release historical executor")
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": (
                retry_request.source.frozen_result_sha256
            ),
            "source_manifest_sha256": (
                retry_request.source.source_manifest_sha256
            ),
            "deterministic": True,
            "replay_schema_version": "replay/v1",
            "artifact_ref": result_ref,
            "artifact_sha256": result_sha256,
        }

    def build_service(bound_state):
        return HistoricalEvaluationService(
            state=bound_state,
            evidence_resolver=resolver,
            rerun_executor=lambda _request: {},
            regrade_executor=lambda _request: {},
            replay_executor=replay,
            claim_stale_after_s=60,
        )

    def invoke(service):
        try:
            return asyncio.run(service.replay(request))
        except HistoricalOperationInProgress as exc:
            return exc

    services = (build_service(state), build_service(second_state))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke, service) for service in services]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with calls_changed:
                    observed_calls = calls
                    if observed_calls >= 2:
                        break
                    calls_changed.wait(timeout=0.05)
                if any(future.done() for future in futures):
                    break
            executor_release.set()
            outcomes = [future.result(timeout=5) for future in futures]
    finally:
        executor_release.set()
        second_state._conn.close()

    assert calls == 1
    assert sum(
        isinstance(outcome, HistoricalOperationReceipt)
        for outcome in outcomes
    ) == 1
    assert sum(
        isinstance(outcome, HistoricalOperationInProgress)
        for outcome in outcomes
    ) == 1
    events = state.read_events_since(
        request.operation_id,
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == expected_events


def _mark_claim_stale(state, operation_id: str) -> None:
    state._conn.execute(
        """UPDATE historical_operation_claims
              SET updated_at=0
            WHERE operation_id=?""",
        (operation_id,),
    )
    state._conn.commit()


def _working_replay(tmp_path, artifacts, calls, name: str):
    def replay(request):
        calls["replay"] += 1
        ref, digest = _write(
            tmp_path,
            f"outputs/{name}.json",
            b'{"decision":"same"}',
        )
        artifacts[ref] = Path(ref).read_bytes()
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": (
                request.source.frozen_result_sha256
            ),
            "source_manifest_sha256": (
                request.source.source_manifest_sha256
            ),
            "deterministic": True,
            "replay_schema_version": "replay/v1",
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    return replay


@pytest.mark.asyncio
async def test_stale_pre_side_effect_claim_is_taken_over_and_completes(
    tmp_path,
):
    state, _service, source, artifacts, calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="stale-running-claim",
    )
    state.reserve_historical_operation(
        operation_id=request.operation_id,
        request_hash=request.request_hash,
        operation=request.operation.value,
    )
    _mark_claim_stale(state, request.operation_id)
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=_working_replay(
            tmp_path,
            artifacts,
            calls,
            "stale-takeover-replay",
        ),
        claim_stale_after_s=1,
    )

    receipt = await service.replay(request)

    assert receipt.result["deterministic"] is True
    assert calls["replay"] == 1
    events = state.read_events_since(
        request.operation_id,
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == [
        "historical_operation.requested",
        "historical_operation.completed",
    ]


@pytest.mark.asyncio
async def test_stale_claim_past_side_effect_boundary_resolves_to_failed(
    tmp_path,
):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="stale-mid-execution-claim",
    )
    state.reserve_historical_operation(
        operation_id=request.operation_id,
        request_hash=request.request_hash,
        operation=request.operation.value,
    )
    state.write_historical_operation_event(
        run_id=request.operation_id,
        kind="historical_operation.requested",
        payload={
            "operation_id": request.operation_id,
            "request_hash": request.request_hash,
            "request": request.to_dict(),
        },
    )
    _mark_claim_stale(state, request.operation_id)
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: pytest.fail(
            "indeterminate claim must never be re-executed"
        ),
        claim_stale_after_s=1,
    )

    with pytest.raises(
        HistoricalOperationIndeterminate,
        match="resolved to a terminal failure",
    ):
        await service.replay(request)

    events = state.read_events_since(
        request.operation_id,
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == [
        "historical_operation.requested",
        "historical_operation.failed",
    ]
    claim = state._conn.execute(
        """SELECT status FROM historical_operation_claims
            WHERE operation_id=?""",
        (request.operation_id,),
    ).fetchone()
    assert claim["status"] == "failed"

    with pytest.raises(HistoricalOperationPreviouslyFailed):
        await service.replay(request)


@pytest.mark.asyncio
async def test_transient_source_verification_failure_stays_retryable(
    tmp_path,
):
    state, _service, source, artifacts, calls = _fixture(tmp_path)
    outages = {"remaining": 1}

    def flaky_resolver(ref):
        if outages["remaining"] > 0:
            outages["remaining"] -= 1
            raise ConnectionError("evidence store outage")
        return artifacts.get(ref)

    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=flaky_resolver,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=_working_replay(
            tmp_path,
            artifacts,
            calls,
            "transient-outage-replay",
        ),
    )
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="transient-outage",
    )

    with pytest.raises(
        HistoricalEvidenceError,
        match="could not be resolved",
    ):
        await service.replay(request)

    assert calls["replay"] == 0
    assert [
        event["kind"]
        for event in state.read_events_since(
            request.operation_id,
            after_event_id=0,
            limit=10,
        )
    ] == ["historical_operation.preflight_released"]

    receipt = await service.replay(request)

    assert receipt.result["deterministic"] is True
    assert calls["replay"] == 1


@pytest.mark.asyncio
async def test_preflight_failure_releases_claim_durably_across_restart(
    tmp_path,
):
    state, _service, source, artifacts, calls = _fixture(tmp_path)
    outages = {"remaining": 1}

    def flaky_resolver(ref):
        if outages["remaining"] > 0:
            outages["remaining"] -= 1
            raise ConnectionError("evidence store outage")
        return artifacts.get(ref)

    def build_service(resolver):
        return HistoricalEvaluationService(
            state=state,
            evidence_resolver=resolver,
            rerun_executor=lambda _request: {},
            regrade_executor=lambda _request: {},
            replay_executor=_working_replay(
                tmp_path,
                artifacts,
                calls,
                "preflight-restart-replay",
            ),
        )

    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="preflight-restart",
    )
    with pytest.raises(
        HistoricalEvidenceError,
        match="could not be resolved",
    ):
        await build_service(flaky_resolver).replay(request)

    receipt = await build_service(artifacts.get).replay(request)

    assert receipt.result["deterministic"] is True
    assert calls["replay"] == 1
    events = state.read_events_since(
        request.operation_id,
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == [
        "historical_operation.preflight_released",
        "historical_operation.requested",
        "historical_operation.completed",
    ]


@pytest.mark.asyncio
async def test_malformed_claim_lease_is_not_treated_as_stale(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="malformed-lease",
    )
    state.reserve_historical_operation(
        operation_id=request.operation_id,
        request_hash=request.request_hash,
        operation=request.operation.value,
    )
    state._conn.execute(
        """UPDATE historical_operation_claims
              SET updated_at='not-a-timestamp'
            WHERE operation_id=?""",
        (request.operation_id,),
    )
    state._conn.commit()
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: pytest.fail(
            "claim with a malformed lease must not be taken over"
        ),
        claim_stale_after_s=1,
    )

    with pytest.raises(HistoricalOperationInProgress):
        await service.replay(request)


@pytest.mark.asyncio
async def test_cancellation_does_not_misrecord_terminal_failure(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    started = asyncio.Event()

    async def cancelled_replay(_request):
        started.set()
        await asyncio.Event().wait()

    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="cancelled-operation",
    )
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=cancelled_replay,
    )
    task = asyncio.create_task(service.replay(request))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    events = state.read_events_since(
        request.operation_id,
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == [
        "historical_operation.requested",
    ]
    claim = state._conn.execute(
        """SELECT status, terminal_event_id
             FROM historical_operation_claims
            WHERE operation_id=?""",
        (request.operation_id,),
    ).fetchone()
    assert dict(claim) == {
        "status": "running",
        "terminal_event_id": None,
    }


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_request_fails(tmp_path):
    _state, service, source, _artifacts, _calls = _fixture(tmp_path)
    first = _request(
        HistoricalOperation.REPLAY,
        source,
        key="shared-key",
    )
    second = _request(
        HistoricalOperation.REPLAY,
        source,
        key="shared-key",
        options={"different": True},
    )
    await service.execute(first)

    with pytest.raises(HistoricalIdempotencyConflict):
        await service.execute(second)


def test_terminal_claim_requires_matching_ledger_event(tmp_path):
    state = State(str(tmp_path / "state.db"))
    row, reserved = state.reserve_historical_operation(
        operation_id="historical-manual",
        request_hash="a" * 64,
        operation="replay",
    )
    assert reserved is True
    assert row["status"] == "running"

    with pytest.raises(
        RuntimeError,
        match="requires its matching ledger event",
    ):
        state.complete_historical_operation(
            operation_id="historical-manual",
            request_hash="a" * 64,
            status="completed",
            terminal_event_id=999,
        )


def test_historical_event_source_and_requested_event_linkage_are_enforced(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    operation_id = "historical-linked"
    request_hash = "a" * 64
    state.reserve_historical_operation(
        operation_id=operation_id,
        request_hash=request_hash,
        operation="replay",
    )

    with pytest.raises(
        ValueError,
        match="historical operation events require the dedicated writer",
    ):
        state.write_event(
            run_id=operation_id,
            source="historical_evaluation",
            kind="historical_operation.completed",
            payload={"request_hash": request_hash},
        )

    requested_event_id = state.write_historical_operation_event(
        run_id=operation_id,
        kind="historical_operation.requested",
        payload={
            "operation_id": operation_id,
            "request_hash": request_hash,
            "request": {"operation": "replay"},
        },
    )
    wrong_link_event_id = state.write_historical_operation_event(
        run_id=operation_id,
        kind="historical_operation.completed",
        payload={
            "operation_id": operation_id,
            "operation": "replay",
            "request_hash": request_hash,
            "requested_event_id": requested_event_id + 100,
        },
    )

    with pytest.raises(
        RuntimeError,
        match="requested event linkage",
    ):
        state.complete_historical_operation(
            operation_id=operation_id,
            request_hash=request_hash,
            status="completed",
            terminal_event_id=wrong_link_event_id,
        )


@pytest.mark.asyncio
async def test_wrong_source_terminal_event_cannot_forge_a_cached_failure(
    tmp_path,
):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="forged-terminal-source",
    )
    state.reserve_historical_operation(
        operation_id=request.operation_id,
        request_hash=request.request_hash,
        operation=request.operation.value,
    )
    requested_event_id = state.write_historical_operation_event(
        run_id=request.operation_id,
        kind="historical_operation.requested",
        payload={
            "operation_id": request.operation_id,
            "request_hash": request.request_hash,
            "request": request.to_dict(),
        },
    )
    with state._write_lock:
        state._conn.execute("BEGIN IMMEDIATE")
        forged_event_id = state._insert_event_unlocked(
            run_id=request.operation_id,
            source="untrusted_component",
            kind="historical_operation.failed",
            payload=state._event_payload(
                run_id=request.operation_id,
                source="untrusted_component",
                kind="historical_operation.failed",
                payload={
                    "operation_id": request.operation_id,
                    "operation": request.operation.value,
                    "request_hash": request.request_hash,
                    "requested_event_id": requested_event_id,
                    "error_type": "Forged",
                    "error": "forged",
                },
            ),
        )
        state._conn.commit()
    assert forged_event_id > requested_event_id
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: pytest.fail(
            "forged terminal must not trigger execution"
        ),
    )

    with pytest.raises(
        HistoricalEvidenceError,
        match="unauthorized source",
    ):
        await service.replay(request)


@pytest.mark.asyncio
async def test_source_hash_mismatch_blocks_before_executor(tmp_path):
    _state, service, source, artifacts, calls = _fixture(tmp_path)
    artifacts[source.source_manifest_ref] = b"tampered"

    with pytest.raises(
        HistoricalEvidenceError,
        match="source run manifest hash mismatch",
    ):
        await service.execute(
            _request(
                HistoricalOperation.RERUN,
                source,
                key="tampered-source",
            )
        )

    assert calls["rerun"] == 0


@pytest.mark.asyncio
async def test_source_bytes_are_revalidated_after_executor_returns(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    calls = 0

    def mutating_replay(request):
        nonlocal calls
        calls += 1
        ref, digest = _write(
            tmp_path,
            "outputs/mutating-replay.json",
            b'{"decision":"same"}',
        )
        artifacts[ref] = Path(ref).read_bytes()
        artifacts[source.source_manifest_ref] = b"mutated-during-execution"
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": (
                request.source.frozen_result_sha256
            ),
            "source_manifest_sha256": (
                request.source.source_manifest_sha256
            ),
            "deterministic": True,
            "replay_schema_version": "replay/v1",
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=mutating_replay,
    )

    with pytest.raises(
        HistoricalEvidenceError,
        match="source run manifest hash mismatch",
    ):
        await service.replay(
            _request(
                HistoricalOperation.REPLAY,
                source,
                key="source-mutated-during-execution",
            )
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_regrade_cannot_change_frozen_result_or_source_grade(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)

    def bad_regrade(_request):
        ref, digest = _write(tmp_path, "outputs/bad-grade.json", b"{}")
        artifacts[ref] = b"{}"
        return {
            "status": "completed",
            "execution_performed": False,
            "source_frozen_result_sha256": "d" * 64,
            "grade_revision_id": "grade-2",
            "supersedes_grade_id": "other-grade",
            "verifier_id": "hidden-tests",
            "verifier_version": "2",
            "verifier_config_sha256": "b" * 64,
            "verifier_implementation_sha256": "c" * 64,
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=bad_regrade,
        replay_executor=lambda _request: {},
    )
    request = _request(
        HistoricalOperation.REGRADE,
        source,
        key="bad-regrade",
    )

    with pytest.raises(
        HistoricalSemanticsError,
        match="changed the frozen source result",
    ):
        await service.regrade(request)

    events = state.read_events_since(
        request.operation_id,
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == [
        "historical_operation.requested",
        "historical_operation.failed",
    ]


@pytest.mark.asyncio
async def test_replay_cannot_execute_agent_or_change_manifest(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)

    def bad_replay(request):
        ref, digest = _write(tmp_path, "outputs/bad-replay.json", b"{}")
        artifacts[ref] = b"{}"
        return {
            "status": "completed",
            "execution_performed": True,
            "source_frozen_result_sha256": (
                request.source.frozen_result_sha256
            ),
            "source_manifest_sha256": "e" * 64,
            "deterministic": True,
            "replay_schema_version": "replay/v1",
            "artifact_ref": ref,
            "artifact_sha256": digest,
        }

    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=bad_replay,
    )

    with pytest.raises(
        HistoricalSemanticsError,
        match="replay must not execute an agent",
    ):
        await service.replay(
            _request(
                HistoricalOperation.REPLAY,
                source,
                key="bad-replay",
            )
        )


@pytest.mark.asyncio
async def test_result_artifact_hash_mismatch_fails_closed(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    ref, _digest = _write(tmp_path, "outputs/tampered.json", b"actual")
    artifacts[ref] = b"actual"

    def bad_rerun(_request):
        return {
            "status": "completed",
            "execution_performed": True,
            "new_run_id": "new-run",
            "run_manifest_ref": "artifact://new-run",
            "run_manifest_sha256": "a" * 64,
            "artifact_ref": ref,
            "artifact_sha256": hashlib.sha256(b"different").hexdigest(),
        }

    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=bad_rerun,
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: {},
    )

    with pytest.raises(
        HistoricalEvidenceError,
        match="historical result artifact hash mismatch",
    ):
        await service.rerun(
            _request(
                HistoricalOperation.RERUN,
                source,
                key="bad-result",
            )
        )


@pytest.mark.asyncio
async def test_rerun_manifest_hash_mismatch_fails_closed(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    result_ref, result_digest = _write(
        tmp_path,
        "outputs/rerun-result.json",
        b'{"status":"passed"}',
    )
    manifest_ref, _manifest_digest = _write(
        tmp_path,
        "outputs/rerun-manifest.json",
        b'{"run":"new-run"}',
    )
    artifacts[result_ref] = Path(result_ref).read_bytes()
    artifacts[manifest_ref] = Path(manifest_ref).read_bytes()

    def bad_rerun(_request):
        return {
            "status": "completed",
            "execution_performed": True,
            "new_run_id": "new-run",
            "run_manifest_ref": manifest_ref,
            "run_manifest_sha256": hashlib.sha256(
                b"different-manifest"
            ).hexdigest(),
            "artifact_ref": result_ref,
            "artifact_sha256": result_digest,
        }

    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=bad_rerun,
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: {},
    )

    with pytest.raises(
        HistoricalEvidenceError,
        match="rerun manifest hash mismatch",
    ):
        await service.rerun(
            _request(
                HistoricalOperation.RERUN,
                source,
                key="bad-rerun-manifest",
            )
        )


@pytest.mark.asyncio
async def test_rerun_manifest_identity_must_match_result_and_source(tmp_path):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    result_ref, result_digest = _write(
        tmp_path,
        "outputs/rerun-identity-result.json",
        b'{"status":"passed"}',
    )
    manifest_ref, manifest_digest = _write(
        tmp_path,
        "outputs/rerun-identity-manifest.json",
        (
            b'{"run_id":"different-run","source_run_id":"source-run",'
            b'"task_id":"task-1"}'
        ),
    )
    artifacts[result_ref] = Path(result_ref).read_bytes()
    artifacts[manifest_ref] = Path(manifest_ref).read_bytes()

    def bad_rerun(_request):
        return {
            "status": "completed",
            "execution_performed": True,
            "new_run_id": "new-run",
            "run_manifest_ref": manifest_ref,
            "run_manifest_sha256": manifest_digest,
            "artifact_ref": result_ref,
            "artifact_sha256": result_digest,
        }

    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=bad_rerun,
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: {},
    )

    with pytest.raises(
        HistoricalSemanticsError,
        match="rerun manifest run_id does not match new_run_id",
    ):
        await service.rerun(
            _request(
                HistoricalOperation.RERUN,
                source,
                key="wrong-rerun-manifest-identity",
            )
        )


@pytest.mark.asyncio
async def test_idempotent_receipt_revalidates_source_and_result_bytes(
    tmp_path,
):
    state, service, source, artifacts, calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="receipt-revalidation",
    )
    receipt = await service.replay(request)
    assert calls["replay"] == 1

    artifacts[receipt.result_ref] = b"tampered-result"
    second_service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: {},
    )
    with pytest.raises(
        HistoricalEvidenceError,
        match="historical result artifact hash mismatch",
    ):
        await second_service.replay(request)

    artifacts[receipt.result_ref] = Path(receipt.result_ref).read_bytes()
    artifacts[source.source_manifest_ref] = b"tampered-source"
    with pytest.raises(
        HistoricalEvidenceError,
        match="source run manifest hash mismatch",
    ):
        await second_service.replay(request)


def _stricter_redact(value):
    if isinstance(value, str):
        return value.replace("same", "[REDACTED_NEW]")
    if isinstance(value, dict):
        return {key: _stricter_redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stricter_redact(item) for item in value]
    return value


@pytest.mark.asyncio
async def test_new_receipts_pin_the_redaction_rules_version(tmp_path):
    _state, service, source, _artifacts, _calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="pinned-redaction-receipt",
    )

    receipt = await service.replay(request)

    assert receipt.redaction_rules_version == (
        RECEIPT_REDACTION_RULES_VERSION
    )
    assert receipt.to_dict()["redaction_rules_version"] == (
        RECEIPT_REDACTION_RULES_VERSION
    )


@pytest.mark.asyncio
async def test_stored_receipts_survive_later_redaction_rule_additions(
    tmp_path,
    monkeypatch,
):
    state, service, source, artifacts, calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="redaction-drift-receipt",
    )
    first = await service.replay(request)
    assert first.result["deterministic"] is True
    assert calls["replay"] == 1

    monkeypatch.setattr(
        "supervisor.historical_evaluation.redact",
        _stricter_redact,
    )
    second = await HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: pytest.fail(
            "stored receipt validation must not re-execute"
        ),
    ).replay(request)

    assert second.to_dict() == first.to_dict()
    assert calls["replay"] == 1


@pytest.mark.asyncio
async def test_legacy_receipt_without_pin_validates_with_frozen_rules(
    tmp_path,
    monkeypatch,
):
    state, _service, source, artifacts, _calls = _fixture(tmp_path)
    request = _request(
        HistoricalOperation.REPLAY,
        source,
        key="legacy-unpinned-receipt",
    )
    state.reserve_historical_operation(
        operation_id=request.operation_id,
        request_hash=request.request_hash,
        operation=request.operation.value,
    )
    requested_event_id = state.write_historical_operation_event(
        run_id=request.operation_id,
        kind="historical_operation.requested",
        payload={
            "operation_id": request.operation_id,
            "request_hash": request.request_hash,
            "request": request.to_dict(),
        },
    )
    result_ref, result_sha256 = _write(
        tmp_path,
        "outputs/legacy-unpinned-result.json",
        b'{"decision":"same"}',
    )
    artifacts[result_ref] = Path(result_ref).read_bytes()
    result = {
        "status": "completed",
        "execution_performed": False,
        "source_frozen_result_sha256": source.frozen_result_sha256,
        "source_manifest_sha256": source.source_manifest_sha256,
        "deterministic": True,
        "replay_schema_version": "replay/v1",
        "artifact_ref": result_ref,
        "artifact_sha256": result_sha256,
        "decision": "same",
    }
    receipt_body = {
        "schema_version": HISTORICAL_OPERATION_RECEIPT_SCHEMA_VERSION,
        "operation_id": request.operation_id,
        "operation": request.operation.value,
        "request_hash": request.request_hash,
        "source_run_id": source.source_run_id,
        "task_id": source.task_id,
        "result_ref": result_ref,
        "result_sha256": result_sha256,
        "result": result,
        "requested_event_id": requested_event_id,
    }
    receipt_hash = hashlib.sha256(
        json.dumps(
            receipt_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    state.write_historical_operation_event(
        run_id=request.operation_id,
        kind="historical_operation.completed",
        payload={**receipt_body, "receipt_hash": receipt_hash},
    )
    monkeypatch.setattr(
        "supervisor.historical_evaluation.redact",
        _stricter_redact,
    )
    service = HistoricalEvaluationService(
        state=state,
        evidence_resolver=artifacts.get,
        rerun_executor=lambda _request: {},
        regrade_executor=lambda _request: {},
        replay_executor=lambda _request: pytest.fail(
            "legacy receipt validation must not re-execute"
        ),
    )

    receipt = await service.replay(request)

    assert receipt.redaction_rules_version is None
    assert receipt.result["decision"] == "same"
    assert receipt.receipt_hash == receipt_hash
