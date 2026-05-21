"""CS19: fixture-only turn/steer route probe.

Public boundary: codex_desktop_ipc_turn_steer_route_probe
    CodexDesktopIpcClient.request("turn/steer", ..., allow_mutating_methods=True)

Forbidden outcomes guarded:
  - turn/start or turn/steer can be sent by default
  - turn/steer route probing touches the real Desktop IPC socket
  - a handler advertises canHandle=true and then hangs the router
"""
from __future__ import annotations

import json
import queue
import socket
import struct
import threading
import uuid
from typing import Any

import pytest

from supervisor.target.codex_desktop_ipc import (
    CodexDesktopIpcClient,
    CodexDesktopIpcError,
)


class _Router:
    def __init__(self) -> None:
        self.clients: list[_RouterSocket] = []
        self.pending: dict[str, _RouterSocket] = {}
        self.discovery: dict[str, tuple[_RouterSocket, _RouterSocket, dict[str, Any]]] = {}

    def connect(self, sock: "_RouterSocket") -> None:
        self.clients.append(sock)

    def handle(self, source: "_RouterSocket", message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "request" and message.get("method") == "initialize":
            source.client_id = str(uuid.uuid4())
            source.queue({
                "type": "response",
                "requestId": message["requestId"],
                "resultType": "success",
                "method": "initialize",
                "result": {"clientId": source.client_id},
            })
            return
        if kind == "request":
            target = next((client for client in self.clients if client is not source), None)
            if target is None:
                source.queue({
                    "type": "response",
                    "requestId": message["requestId"],
                    "resultType": "error",
                    "error": "no-client-found",
                })
                return
            discovery_id = str(uuid.uuid4())
            self.discovery[discovery_id] = (source, target, message)
            target.queue({
                "type": "client-discovery-request",
                "requestId": discovery_id,
                "request": message,
            })
            return
        if kind == "client-discovery-response":
            pending = self.discovery.pop(str(message.get("requestId")), None)
            if pending is None:
                return
            source, target, original = pending
            if message.get("response", {}).get("canHandle") is True:
                self.pending[original["requestId"]] = source
                target.queue(original)
            else:
                source.queue({
                    "type": "response",
                    "requestId": original["requestId"],
                    "resultType": "error",
                    "error": "no-client-found",
                })
            return
        if kind == "response":
            source_client = self.pending.pop(str(message.get("requestId")), None)
            if source_client is not None:
                source_client.queue(message)


class _RouterSocket:
    def __init__(self, router: _Router) -> None:
        self.router = router
        self.client_id: str | None = None
        self.sent_messages: list[dict[str, Any]] = []
        self.incoming: queue.Queue[bytes] = queue.Queue()

    def connect(self, _path: str) -> None:
        self.router.connect(self)

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        length = struct.unpack("<I", data[:4])[0]
        message = json.loads(data[4:4 + length].decode("utf-8"))
        self.sent_messages.append(message)
        self.router.handle(self, message)

    def recv(self, size: int) -> bytes:
        try:
            frame = self.incoming.get(timeout=2)
        except queue.Empty:
            raise socket.timeout()
        if len(frame) > size:
            self.incoming.put(frame[size:])
            return frame[:size]
        return frame

    def queue(self, message: dict[str, Any]) -> None:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        frame = struct.pack("<I", len(body)) + body
        self.incoming.put(frame)


def test_turn_steer_is_blocked_by_default_even_with_fake_socket() -> None:
    router = _Router()
    source_socket = _RouterSocket(router)
    source = CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        socket_factory=lambda *_args, **_kwargs: source_socket,
    )

    with pytest.raises(CodexDesktopIpcError, match="refusing mutating Desktop IPC method"):
        source.request("turn/steer", {
            "threadId": "thread-1",
            "expectedTurnId": "turn-1",
            "input": [{"type": "text", "text": "probe", "text_elements": []}],
        })

    assert source_socket.sent_messages == []


def test_fixture_only_turn_steer_routes_to_advertised_handler_without_real_desktop() -> None:
    router = _Router()
    source_socket = _RouterSocket(router)
    listener_socket = _RouterSocket(router)
    handled: list[dict[str, Any]] = []

    listener = CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        socket_factory=lambda *_args, **_kwargs: listener_socket,
        request_handlers={
            "turn/steer": lambda params: handled.append(params) or {
                "resultType": "error",
                "error": "supervisor-probe-turn-steer-handler",
            },
        },
    )
    source = CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        socket_factory=lambda *_args, **_kwargs: source_socket,
    )

    listener.initialize()
    source.initialize()

    def run_listener() -> None:
        listener.recv_message()
        listener.recv_message()

    listener_thread = threading.Thread(target=run_listener, daemon=True)
    listener_thread.start()

    result = source.request(
        "turn/steer",
        {
            "threadId": "thread-1",
            "expectedTurnId": "turn-1",
            "input": [{"type": "text", "text": "probe", "text_elements": []}],
        },
        allow_mutating_methods=True,
    )

    listener_thread.join(timeout=2)
    assert result["resultType"] == "error"
    assert result["error"] == "supervisor-probe-turn-steer-handler"
    assert handled == [{
        "threadId": "thread-1",
        "expectedTurnId": "turn-1",
        "input": [{"type": "text", "text": "probe", "text_elements": []}],
    }]
    assert any(
        message.get("type") == "client-discovery-response"
        and message.get("response", {}).get("canHandle") is True
        for message in listener_socket.sent_messages
    )
