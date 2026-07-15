from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading

import pytest

import supervisor.run_registry as run_registry
from supervisor.rollout_watcher import RolloutWatcher
from supervisor.run_registry import (
    LaunchReceiptError,
    PENDING_SESSION_SOURCE,
    bind_unambiguous_pending_workflow,
    bind_workflow_target_session,
    consume_launch_receipt,
    load_non_authoritative_session_registration,
    load_session_registration,
    register_submitted_workflow,
    register_workflow_runtime_session,
    reserve_launch_receipt,
    resolve_target_session_id,
    validate_run_registration_authority,
)
from supervisor.state import State
from supervisor.target.types import ScopeContract


def _register(tmp_path: Path, *, session_id: str):
    state = State(str(tmp_path / "state.db"))
    registration = register_submitted_workflow(
        state=state,
        registry_dir=tmp_path / "registry",
        workflow_run_id="workflow-run",
        target_session_id=session_id,
        task_id="task-1",
        task="Do the work.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source="explicit",
    )
    return state, registration


def _register_pending(
    *,
    state: State,
    registry: Path,
    workflow_run_id: str,
    task_id: str,
    cwd: Path,
    target_kind: str = "codex",
) -> None:
    register_submitted_workflow(
        state=state,
        registry_dir=registry,
        workflow_run_id=workflow_run_id,
        target_session_id="",
        task_id=task_id,
        task=f"Do {task_id}.",
        target_kind=target_kind,
        cwd=cwd,
        session_id_source=PENDING_SESSION_SOURCE,
    )


def test_registry_round_trip_stays_inside_registry_root(tmp_path):
    _, registration = _register(tmp_path, session_id="session-123")

    assert registration.registry_path.parent == (tmp_path / "registry").resolve()
    loaded = load_session_registration(tmp_path / "registry", "session-123")
    assert loaded is not None
    assert loaded["workflow_run_id"] == "workflow-run"
    assert loaded["target_session_id"] == "session-123"


@pytest.mark.parametrize(
    "session_id",
    ("../escaped", "nested/session", r"nested\\session", ".", "..", ""),
)
def test_registry_rejects_session_path_traversal_before_writing(
    tmp_path,
    session_id,
):
    state = State(str(tmp_path / "state.db"))

    with pytest.raises(ValueError, match="safe registry filename"):
        register_submitted_workflow(
            state=state,
            registry_dir=tmp_path / "registry",
            workflow_run_id="workflow-run",
            target_session_id=session_id,
            task_id="task-1",
            task="Do the work.",
            target_kind="codex",
            cwd=tmp_path,
            session_id_source="explicit",
        )

    assert state.get_run("workflow-run") is None
    assert not (tmp_path / "escaped.json").exists()


def test_registry_load_rejects_traversal_instead_of_reading_outside(tmp_path):
    (tmp_path / "escaped.json").write_text(
        '{"workflow_run_id":"outside","target_session_id":"../escaped"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="safe registry filename"):
        load_session_registration(tmp_path / "registry", "../escaped")


@pytest.mark.asyncio
async def test_forged_minimal_sidecar_cannot_authorize_rollout_ingestion(
    tmp_path,
):
    sessions_root = tmp_path / "sessions"
    registry = tmp_path / "registry"
    rollout_dir = sessions_root / "2026" / "07" / "15"
    rollout_dir.mkdir(parents=True)
    registry.mkdir()
    session_id = "abababab-1111-2222-3333-444444444444"
    forged_run_id = "workflow-forged-minimal-sidecar"
    rollout = (
        rollout_dir
        / f"rollout-2026-07-15T10-00-00-{session_id}.jsonl"
    )
    rollout.write_text(
        json.dumps({"type": "message", "text": "must quarantine"}) + "\n",
        encoding="utf-8",
    )
    (registry / f"{session_id}.json").write_text(
        json.dumps({
            "workflow_run_id": forged_run_id,
            "target_session_id": session_id,
        }),
        encoding="utf-8",
    )
    state = State(str(tmp_path / "state.db"))
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry),
        state=state,
    )

    assert load_session_registration(registry, session_id) is None
    legacy = load_non_authoritative_session_registration(
        registry,
        session_id,
    )
    assert legacy is not None
    assert legacy["workflow_run_id"] == forged_run_id

    await watcher._drain_file(rollout)

    assert state.get_run(forged_run_id) is None
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE source='rollout'"
    ).fetchone()[0] == 0
    assert state.get_tail_offset(str(rollout)) == 0
    assert len(list(
        (registry / ".rollout-quarantine").glob("*.json")
    )) == 1


def test_registry_rejects_existing_sidecar_symlink_to_outside(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"workflow_run_id":"outside"}', encoding="utf-8")
    (registry / "session-123.json").symlink_to(outside)

    with pytest.raises(ValueError, match="escapes registry root"):
        load_session_registration(registry, "session-123")


def test_registry_rejects_existing_sidecar_symlink_within_root(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    target = registry / "target.json"
    target.write_text(
        json.dumps({
            "workflow_run_id": "workflow-symlink",
            "target_session_id": "session-123",
        }),
        encoding="utf-8",
    )
    (registry / "session-123.json").symlink_to(target)

    assert load_session_registration(registry, "session-123") is None


def test_registry_read_cannot_be_redirected_after_path_validation(
    tmp_path,
    monkeypatch,
):
    _, registration = _register(tmp_path, session_id="session-123")
    sidecar = registration.registry_path
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({
            "workflow_run_id": "workflow-outside",
            "target_session_id": "session-123",
        }),
        encoding="utf-8",
    )
    real_read_text = Path.read_text
    swapped = False

    def swap_before_path_read(path, *args, **kwargs):
        nonlocal swapped
        if path == sidecar and not swapped:
            swapped = True
            sidecar.unlink()
            sidecar.symlink_to(outside)
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", swap_before_path_read)

    loaded = load_session_registration(tmp_path / "registry", "session-123")

    assert loaded is not None
    assert loaded["workflow_run_id"] == "workflow-run"
    assert swapped is False


def test_unknown_target_session_stays_pending_until_runtime_binding(tmp_path):
    target_session_id, source = resolve_target_session_id(
        workflow_run_id="workflow-run",
        target_kind="codex",
        environ={},
    )
    assert target_session_id == ""
    assert source == PENDING_SESSION_SOURCE

    state = State(str(tmp_path / "state.db"))
    registration = register_submitted_workflow(
        state=state,
        registry_dir=tmp_path / "registry",
        workflow_run_id="workflow-run",
        target_session_id=target_session_id,
        task_id="task-1",
        task="Do the work.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=source,
    )

    assert registration.event_payload()["pending"] is True
    assert state.get_run("workflow-run")["session_id"] == "pending:workflow-run"
    assert load_session_registration(tmp_path / "registry", "real-session") is None

    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=tmp_path / "registry",
        workflow_run_id="workflow-run",
        task_id="task-1",
        target_kind="codex",
        cwd=tmp_path,
        now=100,
        ttl_s=60,
    )
    bound = consume_launch_receipt(
        state=state,
        registry_dir=tmp_path / "registry",
        launch_id=receipt.launch_id,
        nonce=receipt.nonce,
        workflow_run_id="workflow-run",
        task_id="task-1",
        target_kind="codex",
        target_session_id="real-session",
        cwd=tmp_path,
        rollout_path="/captured/real-session.jsonl",
        now=101,
    )

    assert bound["pending"] is False
    assert bound["target_session_id"] == "real-session"
    assert bound["launch_id"] == receipt.launch_id
    assert state.get_run("workflow-run")["session_id"] == "real-session"
    assert state.get_run_by_session("real-session")["run_id"] == "workflow-run"
    assert load_session_registration(
        tmp_path / "registry",
        "real-session",
    )["workflow_run_id"] == "workflow-run"


def test_bound_pending_workflow_is_authoritative_for_publication(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-bound-publication",
        task_id="task-bound-publication",
        cwd=tmp_path,
    )
    bind_workflow_target_session(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-bound-publication",
        target_session_id="session-bound-publication",
        source="runtime_result",
        rollout_path="/captured/session-bound-publication.jsonl",
        runtime_run_id="runtime-bound-publication",
        runtime_result_hash="a" * 64,
    )

    validated = validate_run_registration_authority(
        state=state,
        run_id="workflow-bound-publication",
        expected_workflow_run_id="workflow-bound-publication",
        expected_task_id="task-bound-publication",
        expected_target_kind="codex",
        expected_cwd=tmp_path,
        require_workflow_registration=True,
    )

    assert validated["run"]["session_id"] == "session-bound-publication"
    with pytest.raises(RuntimeError, match="pending session_id mismatch"):
        run_registry.validate_pending_workflow_registration_authority(
            state=state,
            run_id="workflow-bound-publication",
            expected_workflow_run_id="workflow-bound-publication",
            expected_task_id="task-bound-publication",
            expected_target_kind="codex",
            expected_cwd=tmp_path,
        )


def test_launch_receipt_rejects_sparse_state_registration(tmp_path):
    registry = (tmp_path / "registry").resolve()
    registry.mkdir()
    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="sparse-workflow",
        session_id="pending:sparse-workflow",
        rollout_path="pending://codex/sparse-workflow",
        task="Do the sparse work.",
        scope=ScopeContract(),
        target_kind="codex",
        config_snapshot=None,
    )
    pending_path = run_registry._pending_registry_path(
        registry,
        "sparse-workflow",
    )
    pending_path.write_text(
        json.dumps({
            "schema_version": "supervisor-run-registration/v2",
            "workflow_run_id": "sparse-workflow",
            "run_id": "sparse-workflow",
            "target_session_id": None,
            "session_id": "pending:sparse-workflow",
            "task_id": "task-sparse",
            "task": "Do the sparse work.",
            "target_kind": "codex",
            "join_key": None,
            "session_id_source": PENDING_SESSION_SOURCE,
            "completion_policy": "workflow_aggregate",
            "registry_path": str(pending_path),
            "pending": True,
            "scope_contract": ScopeContract().to_dict(),
            "config_snapshot": {
                "source": "workflow_submission",
                "schema_version": "supervisor-run-registration/v2",
                "workflow_run_id": "sparse-workflow",
                "target_session_id": None,
                "task_id": "task-sparse",
                "target_kind": "codex",
                "cwd": str(tmp_path.resolve()),
                "session_id_source": PENDING_SESSION_SOURCE,
                "completion_policy": "workflow_aggregate",
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(
        LaunchReceiptError,
        match="workflow run registration config snapshot is missing",
    ):
        reserve_launch_receipt(
            state=state,
            registry_dir=registry,
            workflow_run_id="sparse-workflow",
            task_id="task-sparse",
            target_kind="codex",
            cwd=tmp_path,
        )


def test_ambient_client_session_is_not_target_launch_identity():
    target_session_id, source = resolve_target_session_id(
        workflow_run_id="workflow-run",
        target_kind="codex",
        environ={"CODEX_THREAD_ID": "foreign-client-session"},
    )

    assert target_session_id == ""
    assert source == PENDING_SESSION_SOURCE


def test_same_cwd_never_identifies_or_binds_a_pending_workflow(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    submission_cwd = tmp_path / "submission-workspace"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-live",
        task_id="task-live",
        cwd=submission_cwd,
    )

    bound = bind_unambiguous_pending_workflow(
        state=state,
        registry_dir=registry,
        target_session_id="real-session",
        rollout_path="/captured/real-session.jsonl",
        cwd=submission_cwd,
    )

    assert bound is None
    assert state.get_run("workflow-live")["session_id"] == "pending:workflow-live"
    assert state.get_run_by_session("real-session") is None


def test_first_rollout_does_not_bind_sole_pending_workflow_on_cwd_conflict(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    register_submitted_workflow(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-live",
        target_session_id="",
        task_id="task-live",
        task="Do the live work.",
        target_kind="codex",
        cwd=tmp_path / "expected-repository",
        session_id_source=PENDING_SESSION_SOURCE,
    )

    bound = bind_unambiguous_pending_workflow(
        state=state,
        registry_dir=registry,
        target_session_id="foreign-session",
        rollout_path="/captured/foreign-session.jsonl",
        cwd=tmp_path / "different-repository",
    )

    assert bound is None
    assert state.get_run("workflow-live")["session_id"] == "pending:workflow-live"
    assert state.get_run_by_session("foreign-session") is None


def test_first_rollout_does_not_guess_between_pending_workflows(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    for suffix in ("a", "b"):
        register_submitted_workflow(
            state=state,
            registry_dir=registry,
            workflow_run_id=f"workflow-{suffix}",
            target_session_id="",
            task_id=f"task-{suffix}",
            task=f"Do work {suffix}.",
            target_kind="codex",
            cwd=tmp_path / f"workspace-{suffix}",
            session_id_source=PENDING_SESSION_SOURCE,
        )

    assert (
        bind_unambiguous_pending_workflow(
            state=state,
            registry_dir=registry,
            target_session_id="real-session",
            rollout_path="/captured/real-session.jsonl",
            cwd=None,
        )
        is None
    )
    assert state.get_run_by_session("real-session") is None


def test_valid_launch_receipt_is_single_use_and_nonce_is_not_stored(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-one",
        task_id="task-one",
        cwd=tmp_path,
    )

    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-one",
        task_id="task-one",
        target_kind="codex",
        cwd=tmp_path,
        now=1_000,
        ttl_s=60,
    )

    stored = json.loads(receipt.receipt_path.read_text(encoding="utf-8"))
    assert stored["launch_id"] == receipt.launch_id
    assert stored["nonce_sha256"]
    assert receipt.nonce not in receipt.receipt_path.read_text(encoding="utf-8")

    bound = consume_launch_receipt(
        state=state,
        registry_dir=registry,
        launch_id=receipt.launch_id,
        nonce=receipt.nonce,
        workflow_run_id="workflow-one",
        task_id="task-one",
        target_kind="codex",
        target_session_id="session-one",
        runtime_run_id="runtime-run-one",
        runtime_result_hash="a" * 64,
        cwd=tmp_path,
        now=1_001,
    )

    assert bound["workflow_run_id"] == "workflow-one"
    assert bound["task_id"] == "task-one"
    assert bound["target_kind"] == "codex"
    assert bound["launch_id"] == receipt.launch_id
    assert bound["runtime_run_id"] == "runtime-run-one"
    assert bound["runtime_result_hash"] == "a" * 64
    assert state.get_run("workflow-one")["session_id"] == "session-one"

    with pytest.raises(LaunchReceiptError, match="already consumed"):
        consume_launch_receipt(
            state=state,
            registry_dir=registry,
            launch_id=receipt.launch_id,
            nonce=receipt.nonce,
            workflow_run_id="workflow-one",
            task_id="task-one",
            target_kind="codex",
            target_session_id="session-one",
            cwd=tmp_path,
            now=1_002,
        )


def test_wrong_nonce_and_metadata_cannot_consume_launch_receipt(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-one",
        task_id="task-one",
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-one",
        task_id="task-one",
        target_kind="codex",
        cwd=tmp_path,
        now=2_000,
        ttl_s=60,
    )

    attempts = (
        {"nonce": "wrong-nonce"},
        {"task_id": "task-foreign"},
        {"target_kind": "claude_code"},
        {"cwd": tmp_path / "foreign-cwd"},
    )
    for overrides in attempts:
        kwargs = {
            "state": state,
            "registry_dir": registry,
            "launch_id": receipt.launch_id,
            "nonce": receipt.nonce,
            "workflow_run_id": "workflow-one",
            "task_id": "task-one",
            "target_kind": "codex",
            "target_session_id": "session-one",
            "cwd": tmp_path,
            "now": 2_001,
        }
        kwargs.update(overrides)
        with pytest.raises(LaunchReceiptError):
            consume_launch_receipt(**kwargs)

    assert receipt.receipt_path.exists()
    assert state.get_run("workflow-one")["session_id"] == "pending:workflow-one"


def test_expired_launch_receipt_is_rejected_without_binding(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-expired",
        task_id="task-expired",
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-expired",
        task_id="task-expired",
        target_kind="codex",
        cwd=tmp_path,
        now=3_000,
        ttl_s=5,
    )

    with pytest.raises(LaunchReceiptError, match="expired"):
        consume_launch_receipt(
            state=state,
            registry_dir=registry,
            launch_id=receipt.launch_id,
            nonce=receipt.nonce,
            workflow_run_id="workflow-expired",
            task_id="task-expired",
            target_kind="codex",
            target_session_id="session-expired",
            cwd=tmp_path,
            now=3_005,
        )

    assert state.get_run("workflow-expired")["session_id"] == (
        "pending:workflow-expired"
    )
    assert state.get_run_by_session("session-expired") is None


def test_cross_workflow_launch_receipt_is_rejected_and_remains_usable(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    for suffix in ("a", "b"):
        _register_pending(
            state=state,
            registry=registry,
            workflow_run_id=f"workflow-{suffix}",
            task_id=f"task-{suffix}",
            cwd=tmp_path,
        )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-a",
        task_id="task-a",
        target_kind="codex",
        cwd=tmp_path,
        now=4_000,
        ttl_s=60,
    )

    with pytest.raises(LaunchReceiptError, match="workflow_run_id"):
        consume_launch_receipt(
            state=state,
            registry_dir=registry,
            launch_id=receipt.launch_id,
            nonce=receipt.nonce,
            workflow_run_id="workflow-b",
            task_id="task-b",
            target_kind="codex",
            target_session_id="session-bad",
            cwd=tmp_path,
            now=4_001,
        )

    consume_launch_receipt(
        state=state,
        registry_dir=registry,
        launch_id=receipt.launch_id,
        nonce=receipt.nonce,
        workflow_run_id="workflow-a",
        task_id="task-a",
        target_kind="codex",
        target_session_id="session-a",
        cwd=tmp_path,
        now=4_002,
    )
    assert state.get_run("workflow-a")["session_id"] == "session-a"
    assert state.get_run("workflow-b")["session_id"] == "pending:workflow-b"


def test_two_pending_launches_same_cwd_bind_independently(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    receipts = {}
    for suffix in ("a", "b"):
        workflow_run_id = f"workflow-{suffix}"
        task_id = f"task-{suffix}"
        _register_pending(
            state=state,
            registry=registry,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            cwd=tmp_path,
        )
        receipts[suffix] = reserve_launch_receipt(
            state=state,
            registry_dir=registry,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            target_kind="codex",
            cwd=tmp_path,
            now=5_000,
            ttl_s=60,
        )

    assert receipts["a"].launch_id != receipts["b"].launch_id
    assert receipts["a"].nonce != receipts["b"].nonce

    for suffix in ("b", "a"):
        receipt = receipts[suffix]
        consume_launch_receipt(
            state=state,
            registry_dir=registry,
            launch_id=receipt.launch_id,
            nonce=receipt.nonce,
            workflow_run_id=f"workflow-{suffix}",
            task_id=f"task-{suffix}",
            target_kind="codex",
            target_session_id=f"session-{suffix}",
            cwd=tmp_path,
            now=5_001,
        )

    assert state.get_run("workflow-a")["session_id"] == "session-a"
    assert state.get_run("workflow-b")["session_id"] == "session-b"


def test_concurrent_launch_receipt_consume_has_single_winner(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-race",
        task_id="task-race",
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-race",
        task_id="task-race",
        target_kind="codex",
        cwd=tmp_path,
        now=6_000,
        ttl_s=60,
    )
    barrier = threading.Barrier(8)

    def consume_once(_: int) -> str:
        barrier.wait()
        try:
            consume_launch_receipt(
                state=state,
                registry_dir=registry,
                launch_id=receipt.launch_id,
                nonce=receipt.nonce,
                workflow_run_id="workflow-race",
                task_id="task-race",
                target_kind="codex",
                target_session_id="session-race",
                cwd=tmp_path,
                now=6_001,
            )
        except LaunchReceiptError:
            return "rejected"
        return "bound"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(consume_once, range(8)))

    assert outcomes.count("bound") == 1
    assert outcomes.count("rejected") == 7
    assert state.get_run("workflow-race")["session_id"] == "session-race"
    assert state._conn.execute(
        """SELECT COUNT(*) FROM events
           WHERE run_id=? AND kind='workflow_target_session_bound'""",
        ("workflow-race",),
    ).fetchone()[0] == 1


def test_launch_receipt_store_rejects_namespace_swap_without_writing_replacement(
    tmp_path,
    monkeypatch,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-namespace-swap",
        task_id="task-namespace-swap",
        cwd=tmp_path,
    )
    receipt_root = registry / ".launch-receipts"
    detached_root = registry / ".launch-receipts-detached"
    real_write = run_registry._LaunchReceiptStore.exclusive_write_json
    swapped = False

    def swap_namespace_then_write(store, bucket, name, payload):
        nonlocal swapped
        if not swapped:
            swapped = True
            os.rename(receipt_root, detached_root)
            for child in ("pending", "consumed", "locks"):
                (receipt_root / child).mkdir(parents=True, exist_ok=True)
        return real_write(store, bucket, name, payload)

    monkeypatch.setattr(
        run_registry._LaunchReceiptStore,
        "exclusive_write_json",
        swap_namespace_then_write,
    )

    with pytest.raises(
        LaunchReceiptError,
        match="namespace changed during operation",
    ):
        reserve_launch_receipt(
            state=state,
            registry_dir=registry,
            workflow_run_id="workflow-namespace-swap",
            task_id="task-namespace-swap",
            target_kind="codex",
            cwd=tmp_path,
            now=6_050,
            ttl_s=60,
        )

    assert swapped is True
    assert list((receipt_root / "pending").iterdir()) == []
    assert len(list((detached_root / "pending").glob("*.json"))) == 1
    assert state.get_run("workflow-namespace-swap")["session_id"] == (
        "pending:workflow-namespace-swap"
    )


def test_consuming_launch_receipt_resumes_exact_binding_after_crash(
    tmp_path,
    monkeypatch,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-resume-consuming",
        task_id="task-resume-consuming",
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-resume-consuming",
        task_id="task-resume-consuming",
        target_kind="codex",
        cwd=tmp_path,
        now=6_100,
        ttl_s=60,
    )
    consume_kwargs = {
        "state": state,
        "registry_dir": registry,
        "launch_id": receipt.launch_id,
        "nonce": receipt.nonce,
        "workflow_run_id": "workflow-resume-consuming",
        "task_id": "task-resume-consuming",
        "target_kind": "codex",
        "target_session_id": "session-resume-consuming",
        "runtime_run_id": "runtime-resume-consuming",
        "runtime_result_hash": "a" * 64,
        "cwd": tmp_path,
        "now": 6_101,
    }
    real_bind = run_registry.bind_workflow_target_session

    def crash_after_receipt_claim(**_kwargs):
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(
        run_registry,
        "bind_workflow_target_session",
        crash_after_receipt_claim,
    )
    with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
        consume_launch_receipt(**consume_kwargs)

    consumed_path = next(
        (registry / ".launch-receipts" / "consumed").glob("*.json")
    )
    assert json.loads(consumed_path.read_text(encoding="utf-8"))["status"] == (
        "consuming"
    )
    assert not receipt.receipt_path.exists()
    assert state.get_run("workflow-resume-consuming")["session_id"] == (
        "pending:workflow-resume-consuming"
    )

    monkeypatch.setattr(
        run_registry,
        "bind_workflow_target_session",
        real_bind,
    )
    consume_kwargs["now"] = 6_500  # A claimed receipt remains recoverable after TTL.
    bound = consume_launch_receipt(**consume_kwargs)

    assert bound["target_session_id"] == "session-resume-consuming"
    assert bound["runtime_run_id"] == "runtime-resume-consuming"
    assert bound["runtime_result_hash"] == "a" * 64
    assert json.loads(consumed_path.read_text(encoding="utf-8"))["status"] == (
        "consumed"
    )
    assert state.get_run("workflow-resume-consuming")["session_id"] == (
        "session-resume-consuming"
    )
    assert state._conn.execute(
        """SELECT COUNT(*) FROM events
             WHERE run_id=?
               AND kind='workflow_target_session_bound'""",
        ("workflow-resume-consuming",),
    ).fetchone()[0] == 1


def test_launch_receipt_claim_is_recoverable_before_pending_unlink(
    tmp_path,
    monkeypatch,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    workflow_run_id = "workflow-crash-before-pending-unlink"
    task_id = "task-crash-before-pending-unlink"
    target_session_id = "session-crash-before-pending-unlink"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        target_kind="codex",
        cwd=tmp_path,
        now=6_150,
        ttl_s=60,
    )
    consume_kwargs = {
        "state": state,
        "registry_dir": registry,
        "launch_id": receipt.launch_id,
        "nonce": receipt.nonce,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "target_kind": "codex",
        "target_session_id": target_session_id,
        "runtime_run_id": "runtime-crash-before-pending-unlink",
        "runtime_result_hash": "e" * 64,
        "cwd": tmp_path,
        "now": 6_151,
    }
    real_unlink = run_registry._LaunchReceiptStore.unlink
    crashed = False

    def crash_before_pending_unlink(store, bucket, name):
        nonlocal crashed
        if (
            store.path(bucket, name) == receipt.receipt_path
            and not crashed
        ):
            crashed = True
            raise KeyboardInterrupt("simulated crash before pending unlink")
        return real_unlink(store, bucket, name)

    monkeypatch.setattr(
        run_registry._LaunchReceiptStore,
        "unlink",
        crash_before_pending_unlink,
    )
    with pytest.raises(
        KeyboardInterrupt,
        match="simulated crash before pending unlink",
    ):
        consume_launch_receipt(**consume_kwargs)

    consumed_path = next(
        (registry / ".launch-receipts" / "consumed").glob("*.json")
    )
    claimed = json.loads(consumed_path.read_text(encoding="utf-8"))
    assert claimed["status"] == "consuming"
    assert claimed["target_session_id"] == target_session_id
    assert claimed["runtime_run_id"] == (
        "runtime-crash-before-pending-unlink"
    )
    assert receipt.receipt_path.exists()
    assert state.get_run(workflow_run_id)["session_id"] == (
        f"pending:{workflow_run_id}"
    )

    monkeypatch.setattr(
        run_registry._LaunchReceiptStore,
        "unlink",
        real_unlink,
    )
    consume_kwargs["now"] = 6_500
    bound = consume_launch_receipt(**consume_kwargs)

    assert bound["target_session_id"] == target_session_id
    assert not receipt.receipt_path.exists()
    assert json.loads(consumed_path.read_text(encoding="utf-8"))["status"] == (
        "consumed"
    )


def test_consume_failed_receipt_repairs_missing_binding_event(
    tmp_path,
    monkeypatch,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    workflow_run_id = "workflow-resume-failed"
    task_id = "task-resume-failed"
    target_session_id = "session-resume-failed"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        target_kind="codex",
        cwd=tmp_path,
        now=6_200,
        ttl_s=60,
    )
    consume_kwargs = {
        "state": state,
        "registry_dir": registry,
        "launch_id": receipt.launch_id,
        "nonce": receipt.nonce,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "target_kind": "codex",
        "target_session_id": target_session_id,
        "runtime_run_id": "runtime-resume-failed",
        "runtime_result_hash": "b" * 64,
        "cwd": tmp_path,
        "now": 6_201,
    }
    real_write_event = state.write_event

    def fail_binding_event(*, kind, **kwargs):
        if kind == "workflow_target_session_bound":
            raise RuntimeError("simulated binding-event crash")
        return real_write_event(kind=kind, **kwargs)

    monkeypatch.setattr(state, "write_event", fail_binding_event)
    with pytest.raises(RuntimeError, match="simulated binding-event crash"):
        consume_launch_receipt(**consume_kwargs)

    consumed_path = next(
        (registry / ".launch-receipts" / "consumed").glob("*.json")
    )
    assert json.loads(consumed_path.read_text(encoding="utf-8"))["status"] == (
        "consume_failed"
    )
    assert state.get_run(workflow_run_id)["session_id"] == target_session_id
    assert state._conn.execute(
        """SELECT COUNT(*) FROM events
             WHERE run_id=?
               AND kind='workflow_target_session_bound'""",
        (workflow_run_id,),
    ).fetchone()[0] == 0

    monkeypatch.setattr(state, "write_event", real_write_event)
    consume_kwargs["now"] = 6_500
    consume_launch_receipt(**consume_kwargs)

    assert json.loads(consumed_path.read_text(encoding="utf-8"))["status"] == (
        "consumed"
    )
    assert state._conn.execute(
        """SELECT COUNT(*) FROM events
             WHERE run_id=?
               AND kind='workflow_target_session_bound'""",
        (workflow_run_id,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("field", "mismatched_value"),
    (
        ("nonce", "wrong-nonce"),
        ("workflow_run_id", "workflow-other"),
        ("task_id", "task-other"),
        ("target_kind", "claude_code"),
        ("target_session_id", "session-other"),
        ("runtime_run_id", "runtime-other"),
        ("runtime_result_hash", "c" * 64),
    ),
)
def test_claimed_launch_receipt_rejects_mismatched_replay(
    tmp_path,
    monkeypatch,
    field,
    mismatched_value,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    workflow_run_id = "workflow-reject-claimed-replay"
    task_id = "task-reject-claimed-replay"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        target_kind="codex",
        cwd=tmp_path,
        now=6_300,
        ttl_s=60,
    )
    consume_kwargs = {
        "state": state,
        "registry_dir": registry,
        "launch_id": receipt.launch_id,
        "nonce": receipt.nonce,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "target_kind": "codex",
        "target_session_id": "session-reject-claimed-replay",
        "runtime_run_id": "runtime-reject-claimed-replay",
        "runtime_result_hash": "d" * 64,
        "cwd": tmp_path,
        "now": 6_301,
    }
    real_bind = run_registry.bind_workflow_target_session

    def fail_after_claim(**_kwargs):
        raise RuntimeError("simulated transient bind failure")

    monkeypatch.setattr(
        run_registry,
        "bind_workflow_target_session",
        fail_after_claim,
    )
    with pytest.raises(RuntimeError, match="simulated transient bind failure"):
        consume_launch_receipt(**consume_kwargs)

    replay_kwargs = {**consume_kwargs, field: mismatched_value, "now": 6_500}
    with pytest.raises(LaunchReceiptError, match="mismatch"):
        consume_launch_receipt(**replay_kwargs)

    consumed_path = next(
        (registry / ".launch-receipts" / "consumed").glob("*.json")
    )
    assert json.loads(consumed_path.read_text(encoding="utf-8"))["status"] == (
        "consume_failed"
    )
    assert state.get_run(workflow_run_id)["session_id"] == (
        f"pending:{workflow_run_id}"
    )

    monkeypatch.setattr(
        run_registry,
        "bind_workflow_target_session",
        real_bind,
    )
    consume_kwargs["now"] = 6_501
    consume_launch_receipt(**consume_kwargs)
    assert state.get_run(workflow_run_id)["session_id"] == (
        "session-reject-claimed-replay"
    )


def test_launch_receipt_consume_requires_runtime_cwd(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-cwd-required",
        task_id="task-cwd-required",
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-cwd-required",
        task_id="task-cwd-required",
        target_kind="codex",
        cwd=tmp_path,
        now=7_000,
        ttl_s=60,
    )

    with pytest.raises(LaunchReceiptError, match="cwd is required"):
        consume_launch_receipt(
            state=state,
            registry_dir=registry,
            launch_id=receipt.launch_id,
            nonce=receipt.nonce,
            workflow_run_id="workflow-cwd-required",
            task_id="task-cwd-required",
            target_kind="codex",
            target_session_id="session-cwd-required",
            runtime_run_id="runtime-cwd-required",
            runtime_result_hash="b" * 64,
            cwd=None,
            now=7_001,
        )

    assert receipt.receipt_path.exists()
    assert state.get_run("workflow-cwd-required")["session_id"] == (
        "pending:workflow-cwd-required"
    )
    assert load_session_registration(
        registry,
        "session-cwd-required",
    ) is None


@pytest.mark.parametrize("runtime_cwd_mode", ("missing", "mismatch"))
def test_runtime_session_cwd_must_match_parent_registration(
    tmp_path,
    runtime_cwd_mode,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflow_run_id = f"workflow-runtime-{runtime_cwd_mode}"
    session_id = f"session-runtime-{runtime_cwd_mode}"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id=workflow_run_id,
        task_id=f"task-runtime-{runtime_cwd_mode}",
        cwd=workspace,
    )
    runtime_cwd = (
        ""
        if runtime_cwd_mode == "missing"
        else tmp_path / "different-workspace"
    )

    with pytest.raises(ValueError, match="cwd"):
        register_workflow_runtime_session(
            state=state,
            registry_dir=registry,
            workflow_run_id=workflow_run_id,
            target_session_id=session_id,
            task_id=f"task-runtime-{runtime_cwd_mode}",
            task=f"Do task-runtime-{runtime_cwd_mode}.",
            target_kind="codex",
            cwd=runtime_cwd,
            gate="execution",
            runtime_run_id=f"runtime-run-{runtime_cwd_mode}",
            runtime_result_hash="c" * 64,
        )

    assert load_session_registration(registry, session_id) is None
    assert state._conn.execute(
        "SELECT COUNT(*) FROM runs"
    ).fetchone()[0] == 1


def test_runtime_registration_authority_rejects_forked_session_substitution(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-runtime-fork",
        task_id="task-runtime-fork",
        cwd=tmp_path,
    )
    registration = register_workflow_runtime_session(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-runtime-fork",
        target_session_id="session-runtime-authoritative",
        task_id="task-runtime-fork",
        task="Do task-runtime-fork.",
        target_kind="codex",
        cwd=tmp_path,
        gate="execution",
        runtime_run_id="runtime-run-authoritative",
        runtime_result_hash="c" * 64,
    )
    state._conn.execute(
        "UPDATE runs SET session_id=?, rollout_path=? WHERE run_id=?",
        (
            "session-runtime-forked",
            "/captured/session-runtime-forked.jsonl",
            registration["target_run_id"],
        ),
    )
    state._conn.commit()

    with pytest.raises(
        RuntimeError,
        match="target_session_id mismatch",
    ):
        validate_run_registration_authority(
            state=state,
            run_id=registration["target_run_id"],
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("target_run_id", "target-run-forked"),
        ("target_session_id", "session-runtime-forked"),
        ("workflow_run_id", "workflow-runtime-forked"),
        ("task_id", "task-runtime-forked"),
        ("target_kind", "claude_code"),
        ("cwd", "/tmp/runtime-forked"),
        ("completion_policy", "reusable_session"),
        ("gate", "review"),
        ("runtime_run_id", "runtime-run-forked"),
        ("runtime_result_hash", "d" * 64),
        ("session_id_source", "forked_runtime_result"),
    ),
)
def test_runtime_registration_authority_binds_exact_provenance_fields(
    tmp_path,
    field,
    replacement,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-runtime-exact",
        task_id="task-runtime-exact",
        cwd=tmp_path,
    )
    registration = register_workflow_runtime_session(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-runtime-exact",
        target_session_id="session-runtime-exact",
        task_id="task-runtime-exact",
        task="Do task-runtime-exact.",
        target_kind="codex",
        cwd=tmp_path,
        gate="execution",
        runtime_run_id="runtime-run-exact",
        runtime_result_hash="c" * 64,
    )
    target_run_id = str(registration["target_run_id"])
    snapshot = state.get_run_snapshot(target_run_id)
    assert snapshot is not None
    config_snapshot = json.loads(snapshot["config_json"])
    config_snapshot[field] = replacement
    state._conn.execute(
        "UPDATE run_snapshots SET config_json=? WHERE run_id=?",
        (json.dumps(config_snapshot), target_run_id),
    )
    state._conn.commit()

    with pytest.raises(RuntimeError):
        validate_run_registration_authority(
            state=state,
            run_id=target_run_id,
        )


def test_runtime_registration_rejects_task_different_from_parent(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-runtime-task",
        task_id="task-runtime-task",
        cwd=tmp_path,
    )

    with pytest.raises(
        ValueError,
        match="runtime task does not match",
    ):
        register_workflow_runtime_session(
            state=state,
            registry_dir=registry,
            workflow_run_id="workflow-runtime-task",
            target_session_id="session-runtime-task",
            task_id="task-runtime-task",
            task="Different task text.",
            target_kind="codex",
            cwd=tmp_path,
            gate="execution",
            runtime_run_id="runtime-run-task",
            runtime_result_hash="c" * 64,
        )


def test_runtime_registration_rejects_conflicting_existing_binding_event(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    workflow_run_id = "workflow-runtime-binding-conflict"
    target_session_id = "session-runtime-binding-conflict"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id=workflow_run_id,
        task_id="task-runtime-binding-conflict",
        cwd=tmp_path,
    )
    state.write_event(
        run_id=workflow_run_id,
        source="supervisor",
        kind="workflow_target_session_bound",
        payload={
            "workflow_run_id": workflow_run_id,
            "target_session_id": target_session_id,
            "runtime_result_hash": "d" * 64,
        },
    )

    with pytest.raises(
        RuntimeError,
        match="binding event provenance discrepancy",
    ):
        register_workflow_runtime_session(
            state=state,
            registry_dir=registry,
            workflow_run_id=workflow_run_id,
            target_session_id=target_session_id,
            task_id="task-runtime-binding-conflict",
            task="Do task-runtime-binding-conflict.",
            target_kind="codex",
            cwd=tmp_path,
            gate="execution",
            runtime_run_id="runtime-run-binding-conflict",
            runtime_result_hash="c" * 64,
        )

    assert load_session_registration(registry, target_session_id) is None
    assert state._conn.execute(
        "SELECT COUNT(*) FROM runs"
    ).fetchone()[0] == 1


def test_same_session_retry_rejects_conflicting_runtime_provenance(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-session-retry",
        task_id="task-session-retry",
        cwd=tmp_path,
    )
    first = bind_workflow_target_session(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-session-retry",
        target_session_id="session-retry",
        source="runtime_result",
        runtime_run_id="runtime-run-original",
        runtime_result_hash="d" * 64,
    )
    repeated = bind_workflow_target_session(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-session-retry",
        target_session_id="session-retry",
        source="runtime_result",
        runtime_run_id="runtime-run-original",
        runtime_result_hash="d" * 64,
    )
    assert repeated == first

    with pytest.raises(RuntimeError, match="provenance discrepancy"):
        bind_workflow_target_session(
            state=state,
            registry_dir=registry,
            workflow_run_id="workflow-session-retry",
            target_session_id="session-retry",
            source="runtime_result",
            runtime_run_id="runtime-run-conflict",
            runtime_result_hash="e" * 64,
        )

    loaded = load_session_registration(registry, "session-retry")
    assert loaded is not None
    assert loaded["runtime_run_id"] == first["runtime_run_id"]
    assert loaded["runtime_result_hash"] == first["runtime_result_hash"]
    assert state._conn.execute(
        """SELECT COUNT(*) FROM events
             WHERE run_id=?
               AND kind='workflow_target_session_bound'""",
        ("workflow-session-retry",),
    ).fetchone()[0] == 1


def test_register_submitted_workflow_retry_is_idempotent(tmp_path):
    state, registration = _register(tmp_path, session_id="session-123")

    again = register_submitted_workflow(
        state=state,
        registry_dir=tmp_path / "registry",
        workflow_run_id="workflow-run",
        target_session_id="session-123",
        task_id="task-1",
        task="Do the work.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source="explicit",
    )

    assert again.registry_path == registration.registry_path
    loaded = load_session_registration(tmp_path / "registry", "session-123")
    assert loaded is not None
    assert loaded["workflow_run_id"] == "workflow-run"


def test_register_submitted_workflow_rejects_conflicting_rebinding(tmp_path):
    state, _ = _register(tmp_path, session_id="session-123")

    with pytest.raises(RuntimeError, match="provenance discrepancy"):
        register_submitted_workflow(
            state=state,
            registry_dir=tmp_path / "registry",
            workflow_run_id="workflow-run-2",
            target_session_id="session-123",
            task_id="task-2",
            task="Do different work.",
            target_kind="codex",
            cwd=tmp_path,
            session_id_source="explicit",
        )

    loaded = load_session_registration(tmp_path / "registry", "session-123")
    assert loaded is not None
    assert loaded["workflow_run_id"] == "workflow-run"
    assert loaded["task_id"] == "task-1"


def test_register_submitted_workflow_pending_rejects_conflicting_rebinding(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-pending",
        task_id="task-1",
        cwd=tmp_path,
    )

    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-pending",
        task_id="task-1",
        cwd=tmp_path,
    )

    with pytest.raises(RuntimeError, match="provenance discrepancy"):
        _register_pending(
            state=state,
            registry=registry,
            workflow_run_id="workflow-pending",
            task_id="task-2",
            cwd=tmp_path,
        )


def test_release_launch_receipt_retry_after_partial_release_succeeds(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-run",
        task_id="task-1",
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-run",
        task_id="task-1",
        target_kind="codex",
        cwd=tmp_path,
        now=100,
        ttl_s=60,
    )
    pending_payload = receipt.receipt_path.read_text(encoding="utf-8")

    assert run_registry.release_launch_receipt(
        registry_dir=registry,
        launch_id=receipt.launch_id,
        nonce=receipt.nonce,
        now=101,
    ) is True

    receipt.receipt_path.write_text(pending_payload, encoding="utf-8")

    assert run_registry.release_launch_receipt(
        registry_dir=registry,
        launch_id=receipt.launch_id,
        nonce=receipt.nonce,
        now=102,
    ) is True
    assert not receipt.receipt_path.exists()

    assert run_registry.release_launch_receipt(
        registry_dir=registry,
        launch_id=receipt.launch_id,
        nonce=receipt.nonce,
        now=103,
    ) is True

    with pytest.raises(LaunchReceiptError, match="already claimed"):
        run_registry.release_launch_receipt(
            registry_dir=registry,
            launch_id=receipt.launch_id,
            nonce="wrong-nonce",
            now=104,
        )


def test_release_launch_receipt_still_rejects_consumed_receipt(tmp_path):
    state = State(str(tmp_path / "state.db"))
    registry = tmp_path / "registry"
    _register_pending(
        state=state,
        registry=registry,
        workflow_run_id="workflow-run",
        task_id="task-1",
        cwd=tmp_path,
    )
    receipt = reserve_launch_receipt(
        state=state,
        registry_dir=registry,
        workflow_run_id="workflow-run",
        task_id="task-1",
        target_kind="codex",
        cwd=tmp_path,
        now=100,
        ttl_s=60,
    )
    consume_launch_receipt(
        state=state,
        registry_dir=registry,
        launch_id=receipt.launch_id,
        nonce=receipt.nonce,
        workflow_run_id="workflow-run",
        task_id="task-1",
        target_kind="codex",
        target_session_id="real-session",
        cwd=tmp_path,
        rollout_path="/captured/real-session.jsonl",
        now=101,
    )

    with pytest.raises(LaunchReceiptError, match="already claimed"):
        run_registry.release_launch_receipt(
            registry_dir=registry,
            launch_id=receipt.launch_id,
            nonce=receipt.nonce,
            now=102,
        )
