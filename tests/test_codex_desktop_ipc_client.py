"""CS16: framed Codex Desktop IPC probe client.

Public boundary: codex_desktop_ipc_client
    CodexDesktopIpcClient.initialize/request/broadcast_query_cache_invalidate

Forbidden outcomes guarded:
  - Desktop IPC probes use newline JSON-RPC instead of framed IPC
  - refresh broadcasts start turns or call invisible resume delivery
  - a cache-invalidation broadcast claims visible GUI repaint
  - no-client-found is hidden as a successful thread read
"""
from __future__ import annotations

import json
import socket
import struct
from typing import Any

from supervisor.target.codex_desktop_ipc import CodexDesktopIpcClient


class _FakeFramedSocket:
    def __init__(self) -> None:
        self.connected_to: str | None = None
        self.closed = False
        self.timeout: float | None = None
        self.sent_messages: list[dict[str, Any]] = []
        self._incoming = bytearray()

    def connect(self, path: str) -> None:
        self.connected_to = path

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        length = struct.unpack("<I", data[:4])[0]
        message = json.loads(data[4:4 + length].decode("utf-8"))
        self.sent_messages.append(message)
        if message["type"] == "request" and message["method"] == "initialize":
            self.queue({
                "type": "response",
                "requestId": message["requestId"],
                "resultType": "success",
                "method": "initialize",
                "handledByClientId": "client-123",
                "result": {"clientId": "client-123"},
            })
        elif message["type"] == "request":
            self.queue({
                "type": "response",
                "requestId": message["requestId"],
                "resultType": "error",
                "error": "no-client-found",
            })

    def recv(self, size: int) -> bytes:
        if not self._incoming:
            raise socket.timeout()
        chunk = self._incoming[:size]
        del self._incoming[:size]
        return bytes(chunk)

    def close(self) -> None:
        self.closed = True

    def queue(self, message: dict[str, Any]) -> None:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        self._incoming.extend(struct.pack("<I", len(body)))
        self._incoming.extend(body)


def _client(fake_socket: _FakeFramedSocket) -> CodexDesktopIpcClient:
    return CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        timeout_s=2,
        socket_factory=lambda *_args, **_kwargs: fake_socket,
    )


def test_desktop_ipc_initialize_uses_length_prefixed_frames() -> None:
    fake_socket = _FakeFramedSocket()

    result = _client(fake_socket).initialize()

    assert result == {"clientId": "client-123"}
    assert fake_socket.connected_to == "/tmp/codex-ipc/ipc-test.sock"
    first = fake_socket.sent_messages[0]
    assert first["type"] == "request"
    assert first["method"] == "initialize"
    assert first["version"] == 1
    assert first["params"] == {"clientType": "CODEX_SUPERVISOR"}


def test_desktop_ipc_request_surfaces_no_client_found() -> None:
    fake_socket = _FakeFramedSocket()
    client = _client(fake_socket)
    client.initialize()

    result = client.request(
        "thread/read",
        {
            "threadId": "019e2964-42b5-7ef3-95dc-8d6714482724",
            "includeTurns": False,
        },
    )

    assert result["resultType"] == "error"
    assert result["error"] == "no-client-found"
    assert fake_socket.sent_messages[-1]["method"] == "thread/read"


def test_query_cache_broadcast_never_claims_gui_repaint_or_starts_turn() -> None:
    fake_socket = _FakeFramedSocket()

    result = _client(fake_socket).broadcast_query_cache_invalidate(
        ["recent-conversations"],
    )

    assert result == {
        "delivered": True,
        "reason": "desktop_ipc_broadcast_sent",
        "method": "query-cache-invalidate",
        "desktop_gui_repaint": "unverified",
    }
    methods = [message["method"] for message in fake_socket.sent_messages]
    assert methods == ["initialize", "query-cache-invalidate"]
    assert "turn/start" not in methods
    assert "turn/steer" not in methods
    broadcast = fake_socket.sent_messages[-1]
    assert broadcast["type"] == "broadcast"
    assert broadcast["sourceClientId"] == "client-123"
    assert broadcast["params"] == {"queryKey": ["recent-conversations"]}
