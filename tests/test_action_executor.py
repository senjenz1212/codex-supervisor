"""Ticket 06: action executor routes proposed_actions to DB + adapter + Telegram.

Public boundary: action_executor.execute_actions(proposed, *, state, adapter, telegram_sender)

Rules (from PRD P4, P8 and forbidden-outcomes.md):
  - advise (recommend_scope_violation): record DB row; do NOT call adapter
  - enforce ask_user: record pending row + Telegram ask; do NOT call adapter yet
  - enforce deny_next_write: record row; flag written (adapter not called here)
  - Destructive actions never execute without fresh approval
  - One pending ask_user per run (dedup guard)
  - No live Telegram, no live adapter in these tests (fakes only)
  - No subprocess, httpx, anthropic, openai calls

Forbidden outcomes guarded against:
  - "Destructive action runs without fresh user approval"
  - "Duplicate steering injections race"
  - "Stale or spoofed Telegram callback performs an action"
  - "Direct subprocess calls outside target adapters"
"""
from __future__ import annotations
import json
import subprocess
import httpx
import pytest

from supervisor.state import State


class _FakeTelegramSender:
    """Records Telegram asks without sending anything over the network."""
    def __init__(self):
        self.sent: list[dict] = []

    def __call__(self, *, ask_id: int | None = None, run_id: str,
                 question: str, options: list[str],
                 nonce: str | None = None, expires_at: int | None = None) -> None:
        self.sent.append({
            "ask_id": ask_id, "run_id": run_id,
            "question": question, "options": options,
        })


class _FakeAdapter:
    """Records execute_action calls without touching any subprocess or network."""
    def __init__(self):
        self.calls: list[dict] = []

    async def execute_action(self, action) -> dict:
        self.calls.append({"kind": action.kind if hasattr(action, "kind") else action})
        return {"delivered": True, "reason": "fake"}

    def supports_feature(self, feature: str) -> bool:
        return True


class _UnavailableTelegram:
    def __call__(self, **kwargs):
        raise RuntimeError("telegram unavailable")


class _FailingAdapter:
    async def execute_action(self, action) -> dict:
        raise RuntimeError("adapter down")

    def supports_feature(self, feature: str) -> bool:
        return True


class _Tripwire:
    def __init__(self, name: str): self._name = name
    def __call__(self, *a, **kw):
        raise AssertionError(f"action_executor must not call {self._name}")
    def __getattr__(self, attr):
        raise AssertionError(f"action_executor must not access {self._name}.{attr}")


def _install_tripwires(monkeypatch):
    # pre-import openai before patching httpx (same ordering fix as test_replay_cli.py)
    for mod_name in ("anthropic", "openai"):
        try:
            __import__(mod_name)
        except ImportError:
            pass
    monkeypatch.setattr(httpx, "AsyncClient", _Tripwire("httpx.AsyncClient"))
    monkeypatch.setattr(httpx, "Client",      _Tripwire("httpx.Client"))
    monkeypatch.setattr(subprocess, "run",    _Tripwire("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen",  _Tripwire("subprocess.Popen"))


def _make_state(tmp_path) -> State:
    state = State(str(tmp_path / "test.db"))
    state.upsert_run(run_id="r1", session_id="s1", rollout_path="/tmp",
                     task="test", scope_hints=None)
    return state


_RECOMMEND_ACTION = {
    "kind":               "recommend_scope_violation",
    "status":             "would_recommend",
    "run_id":             "r1",
    "session_id":         "s1",
    "evidence_event_ids": [1, 2],
    "severity":           "warn",
    "summary":            "1 out-of-scope write",
}

_ASK_USER_ACTION = {
    "kind":               "ask_user",
    "status":             "would_ask",
    "run_id":             "r1",
    "session_id":         "s1",
    "evidence_event_ids": [3, 4],
    "severity":           "severe",
    "summary":            "protected write detected",
    "options":            ["Re-anchor", "Allow scope expansion", "Kill run"],
}

_DENY_WRITE_ACTION = {
    "kind":               "deny_next_write",
    "status":             "would_deny_next_write",
    "run_id":             "r1",
    "session_id":         "s1",
    "evidence_event_ids": [5],
    "severity":           "warn",
    "summary":            "1 out-of-scope write",
}

_STEERING_ACTION = {
    "kind":               "inject_steering",
    "status":             "would_inject_steering",
    "run_id":             "r1",
    "session_id":         "s1",
    "message":            "Re-anchor on the auth task.",
    "evidence_event_ids": [6],
    "severity":           "severe",
    "summary":            "agent drifted",
}

_KILL_ACTION = {
    "kind":       "kill",
    "status":     "would_kill",
    "run_id":     "r1",
    "session_id": "s1",
    "summary":    "crashed run",
}

_RESTART_ACTION = {
    "kind":       "restart",
    "status":     "would_restart",
    "run_id":     "r1",
    "session_id": "s1",
    "summary":    "restart target",
}


# ---------- advise: recommend row created, adapter NOT called ----------

def test_advise_recommend_creates_db_row_no_adapter_call(monkeypatch, tmp_path):
    """recommend_scope_violation must write an actions row but not touch the adapter."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions
    results = execute_actions(
        [_RECOMMEND_ACTION], state=state, adapter=adapter, telegram_sender=telegram
    )

    assert len(results) == 1
    r = results[0]
    assert r["action_id"] is not None and r["action_id"] > 0
    assert r["status"] == "recommended"
    assert r["kind"]   == "recommend_scope_violation"

    # No adapter calls, no Telegram asks for a recommendation.
    assert adapter.calls == []
    assert telegram.sent == []

    # DB row exists.
    row = state._conn.execute(
        "SELECT * FROM actions WHERE id=?", (r["action_id"],)
    ).fetchone()
    assert row is not None
    assert row["action_type"] == "recommend_scope_violation"
    assert row["status"]      == "recommended"


# ---------- enforce ask_user: pending row + Telegram ask, no adapter call ----------

def test_enforce_ask_user_creates_pending_row_and_telegram_ask(monkeypatch, tmp_path):
    """ask_user → pending action row + one Telegram ask; adapter not called."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions
    results = execute_actions(
        [_ASK_USER_ACTION], state=state, adapter=adapter, telegram_sender=telegram
    )

    assert len(results) == 1
    r = results[0]
    assert r["status"] == "pending_approval"
    assert r["action_id"] > 0

    # Adapter not called (pending approval).
    assert adapter.calls == []
    # Telegram was asked.
    assert len(telegram.sent) == 1
    ask = telegram.sent[0]
    assert ask["run_id"]  == "r1"
    assert ask["options"] == ["Re-anchor", "Allow scope expansion", "Kill run"]

    # DB row is pending_approval.
    row = state._conn.execute(
        "SELECT * FROM actions WHERE id=?", (r["action_id"],)
    ).fetchone()
    assert row["status"] == "pending_approval"
    assert row["action_type"] == "ask_user"


# ---------- dedup: second ask_user for same run is skipped ----------

def test_dedup_blocks_duplicate_ask_user_same_run(monkeypatch, tmp_path):
    """Only one pending ask_user per run at a time (race-prevention guard)."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions

    # First call → pending.
    r1 = execute_actions([_ASK_USER_ACTION], state=state, adapter=adapter,
                         telegram_sender=telegram)
    assert r1[0]["status"] == "pending_approval"
    assert len(telegram.sent) == 1

    # Second call for same run → skipped.
    r2 = execute_actions([_ASK_USER_ACTION], state=state, adapter=adapter,
                         telegram_sender=telegram)
    assert r2[0]["status"] == "skipped_dedup"
    # Telegram asked only once total.
    assert len(telegram.sent) == 1
    # Adapter still not called.
    assert adapter.calls == []


# ---------- deny_next_write: row recorded, no adapter, no telegram ----------

def test_deny_next_write_records_row_no_adapter(monkeypatch, tmp_path):
    """deny_next_write must record a row; adapter and Telegram are not involved."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions
    results = execute_actions(
        [_DENY_WRITE_ACTION], state=state, adapter=adapter, telegram_sender=telegram
    )

    assert len(results) == 1
    r = results[0]
    assert r["status"] in ("recorded", "pending")  # implementation choice
    assert r["action_id"] > 0
    assert adapter.calls == []
    assert telegram.sent == []


# ---------- no live network from any path ----------

def test_no_live_calls_from_executor(monkeypatch, tmp_path):
    """All three action kinds must never touch network or subprocess."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions
    results = execute_actions(
        [_RECOMMEND_ACTION, _ASK_USER_ACTION, _DENY_WRITE_ACTION],
        state=state, adapter=adapter, telegram_sender=telegram,
    )
    # All three processed without hitting tripwires.
    assert len(results) == 3


# ---------- fresh approval resolves ask_user into one steering adapter call ----------

@pytest.mark.asyncio
async def test_valid_approval_executes_steering_once_and_dedupes(monkeypatch, tmp_path):
    """A valid fresh approval may dispatch steering through the target adapter,
    but duplicate pending steering for the same session must not race."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions, resolve_approval

    # Duplicate proposed steering actions: only one pending approval is created.
    results = execute_actions(
        [_STEERING_ACTION, dict(_STEERING_ACTION)],
        state=state, adapter=adapter, telegram_sender=telegram,
    )
    assert results[0]["status"] == "pending_approval"
    assert results[1]["status"] == "skipped_dedup"
    assert len(telegram.sent) == 1
    assert adapter.calls == []

    ask = state._conn.execute("SELECT * FROM telegram_asks").fetchone()
    assert ask is not None
    assert ask["nonce"], "approval prompts must include a nonce"
    assert ask["expires_at"], "approval prompts must include expiry"

    approved = await resolve_approval(
        ask_id=ask["ask_id"],
        answer="Approve",
        nonce=ask["nonce"],
        state=state,
        adapter=adapter,
    )
    assert approved["status"] == "delivered"
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["kind"] == "inject_steering"

    row = state._conn.execute(
        "SELECT * FROM actions WHERE action_type='inject_steering'"
    ).fetchone()
    assert row["status"] == "delivered"


@pytest.mark.asyncio
async def test_valid_approval_records_adapter_exception(monkeypatch, tmp_path):
    """An approved steering action should fail closed in the ledger if the
    target adapter raises while dispatching it."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions, resolve_approval

    results = execute_actions(
        [_STEERING_ACTION],
        state=state,
        adapter=_FakeAdapter(),
        telegram_sender=telegram,
    )
    assert results[0]["status"] == "pending_approval"
    ask = state._conn.execute("SELECT * FROM telegram_asks").fetchone()

    approved = await resolve_approval(
        ask_id=ask["ask_id"],
        answer="Approve",
        nonce=ask["nonce"],
        state=state,
        adapter=_FailingAdapter(),
    )

    assert approved["status"] == "failed"
    assert approved["adapter_result"]["reason"] == "adapter_exception"
    row = state._conn.execute(
        "SELECT * FROM actions WHERE action_type='inject_steering'"
    ).fetchone()
    assert row["status"] == "failed"
    payload = json.loads(row["payload_json"])
    assert payload["adapter_result"]["reason"] == "adapter_exception"


@pytest.mark.asyncio
async def test_auto_execute_non_destructive_steering_in_enforce_mode(monkeypatch, tmp_path):
    """Aggressive mode may deliver steering without a Telegram tap, but only
    for explicitly allowlisted non-destructive action kinds."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions_async

    results = await execute_actions_async(
        [_STEERING_ACTION, dict(_STEERING_ACTION)],
        state=state,
        adapter=adapter,
        telegram_sender=telegram,
        auto_execute_actions={"inject_steering"},
    )

    assert results == [
        {"action_id": 1, "status": "delivered", "kind": "inject_steering"},
        {"action_id": None, "status": "skipped_dedup", "kind": "inject_steering"},
    ]
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["kind"] == "inject_steering"
    assert telegram.sent == []
    rows = state._conn.execute(
        "SELECT action_type, status, payload_json FROM actions ORDER BY id"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "inject_steering"
    assert rows[0]["status"] == "delivered"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["auto_executed"] is True
    assert payload["requires_approval"] is False
    assert state._conn.execute("SELECT COUNT(*) AS n FROM telegram_asks").fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_auto_execute_never_bypasses_destructive_approval(monkeypatch, tmp_path):
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions_async

    results = await execute_actions_async(
        [_KILL_ACTION],
        state=state,
        adapter=adapter,
        telegram_sender=telegram,
        auto_execute_actions={"kill"},
    )

    assert results[0]["status"] == "pending_approval"
    assert adapter.calls == []
    assert len(telegram.sent) == 1
    row = state._conn.execute("SELECT * FROM actions WHERE action_type='kill'").fetchone()
    assert row["status"] == "pending_approval"
    payload = json.loads(row["payload_json"])
    assert payload["requires_approval"] is True


def test_expired_approval_does_not_dedup_future_steering(monkeypatch, tmp_path):
    """Expired approvals are closed before dedup so a fresh steer can be
    requested without manual DB surgery."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions

    first = execute_actions(
        [_STEERING_ACTION],
        state=state,
        adapter=adapter,
        telegram_sender=telegram,
    )
    assert first[0]["status"] == "pending_approval"
    ask = state._conn.execute("SELECT * FROM telegram_asks").fetchone()
    state._conn.execute(
        "UPDATE telegram_asks SET expires_at=0 WHERE ask_id=?",
        (ask["ask_id"],),
    )
    state._conn.commit()

    second = execute_actions(
        [_STEERING_ACTION],
        state=state,
        adapter=adapter,
        telegram_sender=telegram,
    )

    assert second[0]["status"] == "pending_approval"
    rows = state._conn.execute(
        "SELECT status FROM actions WHERE action_type='inject_steering' ORDER BY id"
    ).fetchall()
    assert [row["status"] for row in rows] == [
        "approval_expired",
        "pending_approval",
    ]


@pytest.mark.asyncio
async def test_stale_or_wrong_nonce_approval_does_not_execute(monkeypatch, tmp_path):
    """Old or spoofed callbacks must not perform an action."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()
    telegram = _FakeTelegramSender()

    from supervisor.action_executor import execute_actions, resolve_approval

    execute_actions([_STEERING_ACTION], state=state, adapter=adapter,
                    telegram_sender=telegram)
    ask = state._conn.execute("SELECT * FROM telegram_asks").fetchone()

    wrong = await resolve_approval(
        ask_id=ask["ask_id"],
        answer="Approve",
        nonce="wrong-nonce",
        state=state,
        adapter=adapter,
    )
    assert wrong["status"] == "rejected"
    assert adapter.calls == []

    # Expire the still-pending ask and verify stale approval also fails closed.
    state._conn.execute(
        "UPDATE telegram_asks SET expires_at=0 WHERE ask_id=?",
        (ask["ask_id"],),
    )
    state._conn.commit()
    stale = await resolve_approval(
        ask_id=ask["ask_id"],
        answer="Approve",
        nonce=ask["nonce"],
        state=state,
        adapter=adapter,
    )
    assert stale["status"] == "rejected"
    assert adapter.calls == []


def test_destructive_actions_fail_closed_when_telegram_unavailable(monkeypatch, tmp_path):
    """kill/restart require fresh approval. If Telegram is unavailable, they
    are recorded as approval_unavailable and never sent to the adapter."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()

    from supervisor.action_executor import execute_actions

    results = execute_actions(
        [_KILL_ACTION, _RESTART_ACTION],
        state=state,
        adapter=adapter,
        telegram_sender=_UnavailableTelegram(),
    )
    assert [r["status"] for r in results] == [
        "approval_unavailable",
        "approval_unavailable",
    ]
    assert adapter.calls == []

    rows = state._conn.execute(
        "SELECT action_type, status, payload_json FROM actions ORDER BY id"
    ).fetchall()
    assert [r["action_type"] for r in rows] == ["kill", "restart"]
    assert [r["status"] for r in rows] == [
        "approval_unavailable",
        "approval_unavailable",
    ]
    for row in rows:
        payload = json.loads(row["payload_json"])
        assert payload["requires_approval"] is True


def test_destructive_actions_fail_closed_when_telegram_sender_missing(monkeypatch, tmp_path):
    """A missing Telegram sender is also an unavailable approval channel."""
    _install_tripwires(monkeypatch)
    state = _make_state(tmp_path)
    adapter = _FakeAdapter()

    from supervisor.action_executor import execute_actions

    results = execute_actions(
        [_KILL_ACTION],
        state=state,
        adapter=adapter,
        telegram_sender=None,
    )
    assert results[0]["status"] == "approval_unavailable"
    assert adapter.calls == []
