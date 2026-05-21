"""Issue 10 / CS10: passive Codex Desktop status sync through app-server.

Public boundary: codex_app_server_status_sync
    CodexAdapter.execute_action(TargetAction(kind="append_status_item", ...))

Forbidden outcomes guarded:
  - passive status sync calls codex exec resume or codex resume
  - status sync starts a Codex turn instead of app-server thread/inject_items
  - secrets are written into the injected item
  - app-server failure raises out of the adapter
"""
from __future__ import annotations

import asyncio
import json

import pytest

from supervisor.target.codex import CodexAdapter
from supervisor.target.types import TargetAction


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeStdout:
    def __init__(self, lines: list[dict]) -> None:
        self._lines = [
            (json.dumps(line) + "\n").encode("utf-8")
            for line in lines
        ]

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _HangingStdout:
    async def readline(self) -> bytes:
        await asyncio.sleep(5)
        return b""


class _FakeProc:
    def __init__(self, lines: list[dict]) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStdout([])
        self.returncode: int | None = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _ok_response(request_id: int, result: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result or {},
    }


def _written_requests(proc: _FakeProc) -> list[dict]:
    return [json.loads(line.decode("utf-8")) for line in proc.stdin.writes]


@pytest.mark.asyncio
async def test_append_status_item_uses_app_server_inject_items_not_resume(monkeypatch):
    """First RED: the public adapter action must speak app-server JSON-RPC,
    not `codex exec resume`.
    """
    created: list[_FakeProc] = []
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*argv, stdin=None, stdout=None, stderr=None, **kwargs):
        calls.append(tuple(argv))
        proc = _FakeProc([
            {"jsonrpc": "2.0", "method": "thread/started", "params": {}},
            _ok_response(1, {
                "codexHome": "/tmp/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
                "userAgent": "fake-codex",
            }),
            _ok_response(2, {"thread": {"id": "sess-vela", "name": "Vela"}}),
            _ok_response(3, {}),
        ])
        created.append(proc)
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({
        "cli_command": "/Applications/Codex.app/Contents/Resources/codex",
        "app_server_transport": "stdio",
        "app_server_timeout_s": 3,
    })

    result = await adapter.execute_action(TargetAction(
        kind="append_status_item",
        session_id="sess-vela",
        payload={
            "message": "18c.5 grill is approved and ready for explicit worker launch.",
            "source": "supervisor_status",
        },
    ))

    assert result["delivered"] is True
    assert result["reason"] == "app_server_injected"
    assert result["desktop_gui_repaint"] == "unverified"
    assert calls == [
        ("/Applications/Codex.app/Contents/Resources/codex", "app-server", "--listen", "stdio://"),
    ]
    assert all("resume" not in arg for call in calls for arg in call)

    requests = _written_requests(created[0])
    assert [r["method"] for r in requests] == [
        "initialize",
        "thread/resume",
        "thread/inject_items",
    ]
    assert requests[0]["params"]["capabilities"]["experimentalApi"] is True

    assert requests[1]["params"]["threadId"] == "sess-vela"
    inject = requests[2]
    assert inject["params"]["threadId"] == "sess-vela"
    items = inject["params"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "message"
    assert item["role"] == "assistant"
    assert item["content"][0]["type"] == "output_text"
    assert "Supervisor status" in item["content"][0]["text"]
    assert "18c.5 grill is approved" in item["content"][0]["text"]


@pytest.mark.asyncio
async def test_append_status_item_redacts_before_app_server_write(monkeypatch):
    created: list[_FakeProc] = []

    async def fake_exec(*argv, stdin=None, stdout=None, stderr=None, **kwargs):
        proc = _FakeProc([
            _ok_response(1, {
                "codexHome": "/tmp/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
                "userAgent": "fake-codex",
            }),
            _ok_response(2, {"thread": {"id": "sess-vela", "name": "Vela"}}),
            _ok_response(3, {}),
        ])
        created.append(proc)
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({
        "cli_command": "/tmp/fake-codex",
        "app_server_transport": "stdio",
    })

    result = await adapter.execute_action(TargetAction(
        kind="append_status_item",
        session_id="sess-vela",
        payload={
            "message": "Do not leak ANTHROPIC_API_KEY=sk-ant-supersecret12345",
            "source": "supervisor_status",
        },
    ))

    assert result["delivered"] is True
    raw_wire_text = b"".join(created[0].stdin.writes).decode("utf-8")
    assert "sk-ant-supersecret12345" not in raw_wire_text
    assert "[REDACTED]" in raw_wire_text


@pytest.mark.asyncio
async def test_append_status_item_app_server_failure_returns_delivered_false(monkeypatch):
    async def fake_exec(*argv, stdin=None, stdout=None, stderr=None, **kwargs):
        raise RuntimeError("app-server unavailable")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({
        "cli_command": "/tmp/fake-codex",
        "app_server_transport": "stdio",
    })

    result = await adapter.execute_action(TargetAction(
        kind="append_status_item",
        session_id="sess-vela",
        payload={"message": "status"},
    ))

    assert result["delivered"] is False
    assert result["reason"] == "app_server_error"
    assert result["feature"] == "append_status_item"
    assert result["target"] == "codex"


@pytest.mark.asyncio
async def test_append_status_item_missing_binary_returns_delivered_false(monkeypatch):
    async def fake_exec(*argv, stdin=None, stdout=None, stderr=None, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({
        "cli_command": "/tmp/fake-codex",
        "app_server_transport": "stdio",
    })

    result = await adapter.execute_action(TargetAction(
        kind="append_status_item",
        session_id="sess-vela",
        payload={"message": "status"},
    ))

    assert result["delivered"] is False
    assert result["reason"] == "codex_binary_not_found"
    assert result["feature"] == "append_status_item"
    assert result["target"] == "codex"


@pytest.mark.asyncio
async def test_append_status_item_timeout_reports_method(monkeypatch):
    async def fake_exec(*argv, stdin=None, stdout=None, stderr=None, **kwargs):
        proc = _FakeProc([])
        proc.stdout = _HangingStdout()
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({
        "cli_command": "/tmp/fake-codex",
        "app_server_transport": "proxy",
        "app_server_timeout_s": 1,
    })

    result = await adapter.execute_action(TargetAction(
        kind="append_status_item",
        session_id="sess-vela",
        payload={"message": "status"},
    ))

    assert result["delivered"] is False
    assert result["reason"] == "app_server_error"
    assert "timed out waiting for initialize response" in result["error"]


@pytest.mark.asyncio
async def test_append_status_item_defaults_to_proxy_transport(monkeypatch):
    created: list[_FakeProc] = []
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*argv, stdin=None, stdout=None, stderr=None, **kwargs):
        calls.append(tuple(argv))
        proc = _FakeProc([
            _ok_response(1, {
                "codexHome": "/tmp/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
                "userAgent": "fake-codex",
            }),
            _ok_response(2, {"thread": {"id": "sess-vela", "name": "Vela"}}),
            _ok_response(3, {}),
        ])
        created.append(proc)
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({"cli_command": "/tmp/fake-codex"})

    result = await adapter.execute_action(TargetAction(
        kind="append_status_item",
        session_id="sess-vela",
        payload={"message": "status"},
    ))

    assert result["delivered"] is True
    assert calls == [("/tmp/fake-codex", "app-server", "proxy")]
    requests = _written_requests(created[0])
    assert [r["method"] for r in requests] == [
        "initialize",
        "thread/resume",
        "thread/inject_items",
    ]
