from __future__ import annotations

import json
import socket
from pathlib import Path


THREAD_ID = "019e2964-42b5-7ef3-95dc-8d6714482724"


def _stream_patch(secret: str = "do-not-store") -> dict:
    return {
        "type": "broadcast",
        "method": "thread-stream-state-changed",
        "sourceClientId": "desktop",
        "version": 6,
        "params": {
            "conversationId": THREAD_ID,
            "hostId": "local",
            "change": {
                "type": "patches",
                "patches": [{
                    "path": ["turns", 1, "items", 2],
                    "value": {"text": secret},
                }],
            },
        },
    }


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(message) for message in messages) + "\n")


def test_probe_diff_cli_writes_decision_json_without_values(tmp_path: Path) -> None:
    from supervisor.desktop_ipc_probe import main

    cold = tmp_path / "cold.jsonl"
    normal = tmp_path / "normal.jsonl"
    out = tmp_path / "diff.json"
    _write_jsonl(cold, [
        {"type": "request", "method": "thread/hydrate", "params": {"raw": "hide-me"}},
    ])
    _write_jsonl(normal, [
        _stream_patch("normal-turn-secret"),
        {"type": "request", "method": "turn/start", "params": {"prompt": "hide-me"}},
    ])

    rc = main([
        "diff",
        "--cold-start", str(cold),
        "--normal-turn", str(normal),
        "--output", str(out),
    ])

    assert rc == 0
    data = json.loads(out.read_text())
    assert data["recommended_next_step"] == "probe_candidate_live_turn_method"
    serialized = out.read_text()
    assert "hide-me" not in serialized
    assert "normal-turn-secret" not in serialized


def test_probe_capture_cli_writes_sanitized_frames(monkeypatch, tmp_path: Path) -> None:
    import supervisor.desktop_ipc_probe as probe
    from supervisor.target.codex_desktop_ipc import CodexDesktopIpcError

    out = tmp_path / "capture.jsonl"

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            self.messages = [
                CodexDesktopIpcError("desktop IPC receive failed: timed out"),
                _stream_patch("capture-secret"),
                {
                    "type": "request",
                    "method": "turn/start",
                    "params": {"prompt": "do not store this prompt"},
                },
            ]
            self.closed = False

        def recv_message(self) -> dict:
            if not self.messages:
                raise socket.timeout()
            message = self.messages.pop(0)
            if isinstance(message, Exception):
                raise message
            return message

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(probe, "CodexDesktopIpcClient", FakeClient)

    rc = probe.main([
        "capture",
        "--phase", "normal_turn",
        "--output", str(out),
        "--limit", "2",
        "--duration-s", "1",
    ])

    assert rc == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines == [
        {
            "type": "broadcast",
            "method": "thread-stream-state-changed",
            "sourceClientId": "desktop",
            "version": 6,
            "params": {
                "conversationId": THREAD_ID,
                "hostId": "local",
                "change": {
                    "type": "patches",
                    "patches": [{"path": "turns/1/items/2"}],
                },
            },
        },
        {
            "type": "request",
            "method": "turn/start",
        },
    ]
    serialized = out.read_text()
    assert "capture-secret" not in serialized
    assert "do not store this prompt" not in serialized
