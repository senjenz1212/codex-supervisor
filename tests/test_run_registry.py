from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from supervisor.run_registry import (
    LaunchReceiptError,
    PENDING_SESSION_SOURCE,
    bind_unambiguous_pending_workflow,
    consume_launch_receipt,
    load_session_registration,
    register_submitted_workflow,
    reserve_launch_receipt,
    resolve_target_session_id,
)
from supervisor.state import State


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


def test_registry_rejects_existing_sidecar_symlink_to_outside(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"workflow_run_id":"outside"}', encoding="utf-8")
    (registry / "session-123.json").symlink_to(outside)

    with pytest.raises(ValueError, match="escapes registry root"):
        load_session_registration(registry, "session-123")


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
        cwd=tmp_path,
        now=1_001,
    )

    assert bound["workflow_run_id"] == "workflow-one"
    assert bound["task_id"] == "task-one"
    assert bound["target_kind"] == "codex"
    assert bound["launch_id"] == receipt.launch_id
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
