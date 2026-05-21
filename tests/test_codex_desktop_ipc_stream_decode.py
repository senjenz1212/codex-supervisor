"""CS17: decode Desktop thread-stream-state-changed captures.

Public boundary: codex_desktop_ipc_stream_decode
    summarize_thread_stream_capture(path)
    CodexDesktopIpcClient.capture_messages(...)

Forbidden outcomes guarded:
  - stream decode starts or steers a turn
  - snapshot/patch captures are treated as verified GUI repaint
  - non-thread-stream IPC messages are misclassified as thread updates
"""
from __future__ import annotations

import json
import socket
import struct
from pathlib import Path
from typing import Any

from supervisor.target.codex_desktop_ipc import (
    CodexDesktopIpcClient,
    summarize_thread_stream_capture,
    summarize_thread_stream_state_changed,
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


def _snapshot_message() -> dict[str, Any]:
    return {
        "type": "broadcast",
        "method": "thread-stream-state-changed",
        "sourceClientId": "desktop",
        "version": 6,
        "params": {
            "conversationId": "019e2964-42b5-7ef3-95dc-8d6714482724",
            "hostId": "local",
            "change": {
                "type": "snapshot",
                "conversationState": {
                    "id": "019e2964-42b5-7ef3-95dc-8d6714482724",
                    "title": "Vela Slack bot",
                    "turns": [],
                },
            },
        },
    }


def _patches_message() -> dict[str, Any]:
    return {
        "type": "broadcast",
        "method": "thread-stream-state-changed",
        "sourceClientId": "desktop",
        "version": 6,
        "params": {
            "conversationId": "019e2964-42b5-7ef3-95dc-8d6714482724",
            "hostId": "local",
            "change": {
                "type": "patches",
                "patches": [
                    {
                        "op": "add",
                        "path": ["turns", 0],
                        "value": {"turnId": "turn-1", "status": "inProgress"},
                    },
                    {
                        "op": "replace",
                        "path": ["turns", 0, "status"],
                        "value": "completed",
                    },
                ],
            },
        },
    }


def test_replay_decodes_snapshot_and_patch_thread_stream_frames(tmp_path: Path) -> None:
    capture = tmp_path / "desktop-ipc.jsonl"
    capture.write_text(
        "\n".join([
            json.dumps({"type": "broadcast", "method": "client-status-changed"}),
            json.dumps(_snapshot_message()),
            json.dumps(_patches_message()),
        ])
        + "\n",
    )

    summaries = summarize_thread_stream_capture(capture)

    assert summaries == [
        {
            "method": "thread-stream-state-changed",
            "conversation_id": "019e2964-42b5-7ef3-95dc-8d6714482724",
            "host_id": "local",
            "source_client_id": "desktop",
            "version": 6,
            "change_type": "snapshot",
            "has_conversation_state": True,
            "patch_count": 0,
            "patch_paths": [],
            "desktop_gui_repaint": "unverified",
        },
        {
            "method": "thread-stream-state-changed",
            "conversation_id": "019e2964-42b5-7ef3-95dc-8d6714482724",
            "host_id": "local",
            "source_client_id": "desktop",
            "version": 6,
            "change_type": "patches",
            "has_conversation_state": False,
            "patch_count": 2,
            "patch_paths": ["turns/0", "turns/0/status"],
            "desktop_gui_repaint": "unverified",
        },
    ]


def test_thread_stream_decoder_ignores_non_matching_messages() -> None:
    assert summarize_thread_stream_state_changed({
        "type": "broadcast",
        "method": "query-cache-invalidate",
        "params": {"queryKey": ["recent-conversations"]},
    }) is None


def test_capture_messages_filters_thread_stream_without_starting_or_steering() -> None:
    fake_socket = _QueuedFramedSocket([
        {"type": "broadcast", "method": "client-status-changed", "params": {}},
        _snapshot_message(),
    ])
    client = CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
    )

    messages = client.capture_messages(
        limit=1,
        methods={"thread-stream-state-changed"},
    )

    assert messages == [_snapshot_message()]
    sent_methods = [message["method"] for message in fake_socket.sent_messages]
    assert sent_methods == ["initialize"]
    assert "turn/start" not in sent_methods
    assert "turn/steer" not in sent_methods


def test_client_discovery_request_gets_false_observer_response() -> None:
    fake_socket = _QueuedFramedSocket([
        {
            "type": "client-discovery-request",
            "requestId": "discovery-1",
            "request": {
                "type": "request",
                "method": "ide-context",
                "params": {"workspaceRoot": "/Users/sam.zhang/Documents/codex-supervisor"},
            },
        },
    ])
    client = CodexDesktopIpcClient(
        socket_path="/tmp/codex-ipc/ipc-test.sock",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
    )

    message = client.recv_message()

    assert message["type"] == "client-discovery-request"
    discovery_responses = [
        sent for sent in fake_socket.sent_messages
        if sent.get("type") == "client-discovery-response"
    ]
    assert discovery_responses == [{
        "type": "client-discovery-response",
        "requestId": "discovery-1",
        "response": {"canHandle": False},
    }]
