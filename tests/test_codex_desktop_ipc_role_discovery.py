"""CS18: Desktop IPC client-discovery role enumeration.

Public boundary: codex_desktop_ipc_role_discovery
    summarize_client_discovery_capture(path)
    CodexDesktopIpcClient(... request_handlers=...)

Forbidden outcomes guarded:
  - observer claims canHandle=true without a handler for the method
  - client-discovery requests are stored with sensitive parameter values
  - incoming handled requests hang without a response
  - role discovery starts or steers a Codex turn
"""
from __future__ import annotations

import json
import socket
import struct
from pathlib import Path
from typing import Any

from supervisor.target.codex_desktop_ipc import (
    CodexDesktopIpcClient,
    summarize_client_discovery_capture,
    summarize_client_discovery_request,
)


class _QueuedFramedSocket:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self._incoming = bytearray()
        self._messages_after_initialize = list(messages)

    def connect(self, _path: str) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

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
                "result": {"clientId": "client-123"},
            })
            for queued in self._messages_after_initialize:
                self.queue(queued)

    def recv(self, size: int) -> bytes:
        if not self._incoming:
            raise socket.timeout()
        chunk = self._incoming[:size]
        del self._incoming[:size]
        return bytes(chunk)

    def queue(self, message: dict[str, Any]) -> None:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        self._incoming.extend(struct.pack("<I", len(body)))
        self._incoming.extend(body)


def _discovery_request(method: str = "ide-context") -> dict[str, Any]:
    return {
        "type": "client-discovery-request",
        "requestId": "discovery-1",
        "request": {
            "type": "request",
            "requestId": "inner-1",
            "sourceClientId": "desktop-client",
            "version": 0,
            "method": method,
            "params": {
                "workspaceRoot": "/Users/sam.zhang/Documents/codex-supervisor",
                "ANTHROPIC_API_KEY": "sk-ant-do-not-store",
            },
        },
    }


def test_client_discovery_capture_summarizes_methods_without_param_values(tmp_path: Path) -> None:
    capture = tmp_path / "desktop-ipc-discovery.jsonl"
    capture.write_text(
        "\n".join([
            json.dumps({"type": "broadcast", "method": "thread-stream-state-changed"}),
            json.dumps(_discovery_request()),
        ])
        + "\n",
    )

    summaries = summarize_client_discovery_capture(capture)

    assert summaries == [{
        "type": "client-discovery-request",
        "request_id": "discovery-1",
        "method": "ide-context",
        "version": 0,
        "source_client_id": "desktop-client",
        "param_keys": ["ANTHROPIC_API_KEY", "workspaceRoot"],
    }]
    assert "sk-ant-do-not-store" not in json.dumps(summaries)


def test_client_discovery_request_summary_ignores_other_messages() -> None:
    assert summarize_client_discovery_request({
        "type": "broadcast",
        "method": "thread-stream-state-changed",
    }) is None


def test_client_discovery_can_handle_false_without_registered_handler() -> None:
    fake_socket = _QueuedFramedSocket([_discovery_request()])
    client = CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
    )

    client.recv_message()

    assert client.discovery_requests == [{
        "type": "client-discovery-request",
        "request_id": "discovery-1",
        "method": "ide-context",
        "version": 0,
        "source_client_id": "desktop-client",
        "param_keys": ["ANTHROPIC_API_KEY", "workspaceRoot"],
    }]
    discovery_response = fake_socket.sent_messages[-1]
    assert discovery_response == {
        "type": "client-discovery-response",
        "requestId": "discovery-1",
        "response": {"canHandle": False},
    }


def test_client_discovery_can_handle_true_only_with_registered_handler() -> None:
    fake_socket = _QueuedFramedSocket([_discovery_request()])
    client = CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        request_handlers={
            "ide-context": lambda _params: {
                "resultType": "error",
                "error": "unsupported-by-supervisor-observer",
            },
        },
    )

    client.recv_message()

    discovery_response = fake_socket.sent_messages[-1]
    assert discovery_response == {
        "type": "client-discovery-response",
        "requestId": "discovery-1",
        "response": {"canHandle": True},
    }


def test_registered_handler_answers_forwarded_request_without_turn_start_or_steer() -> None:
    fake_socket = _QueuedFramedSocket([
        {
            "type": "request",
            "requestId": "forwarded-1",
            "sourceClientId": "desktop-client",
            "version": 0,
            "method": "ide-context",
            "params": {"workspaceRoot": "/Users/sam.zhang/Documents/codex-supervisor"},
        },
    ])
    client = CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        request_handlers={
            "ide-context": lambda params: {
                "resultType": "success",
                "result": {"workspaceRoot": params["workspaceRoot"]},
            },
        },
    )

    message = client.recv_message()

    assert message["method"] == "ide-context"
    response = fake_socket.sent_messages[-1]
    assert response == {
        "type": "response",
        "requestId": "forwarded-1",
        "resultType": "success",
        "method": "ide-context",
        "result": {"workspaceRoot": "/Users/sam.zhang/Documents/codex-supervisor"},
    }
    sent_methods = [sent.get("method") for sent in fake_socket.sent_messages]
    assert "turn/start" not in sent_methods
    assert "turn/steer" not in sent_methods
