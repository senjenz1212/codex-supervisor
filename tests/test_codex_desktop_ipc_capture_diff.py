"""CS20: cold-start vs normal-turn Desktop IPC capture diff.

Public boundary: codex_desktop_ipc_capture_diff
    summarize_desktop_ipc_capture(path, phase=...)
    diff_desktop_ipc_captures(cold_start_path, normal_turn_path)

Forbidden outcomes guarded:
  - capture analysis stores IPC parameter values or patch values
  - fixture analysis claims verified GUI repaint
  - capture diff requires live Desktop IPC or mutating turn methods
"""
from __future__ import annotations

import json
from pathlib import Path

from supervisor.target.codex_desktop_ipc import (
    diff_desktop_ipc_captures,
    sanitize_desktop_ipc_message,
    summarize_desktop_ipc_capture,
)


THREAD_ID = "019e2964-42b5-7ef3-95dc-8d6714482724"


def _snapshot() -> dict:
    return {
        "type": "broadcast",
        "method": "thread-stream-state-changed",
        "sourceClientId": "desktop",
        "version": 6,
        "params": {
            "conversationId": THREAD_ID,
            "hostId": "local",
            "change": {
                "type": "snapshot",
                "conversationState": {
                    "id": THREAD_ID,
                    "title": "Vela Slack bot",
                    "turns": [],
                    "secret": "sk-ant-do-not-store",
                },
            },
        },
    }


def _patches() -> dict:
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
                "patches": [
                    {
                        "op": "add",
                        "path": ["turns", 72, "items", 49],
                        "value": {
                            "text": "visible GUI text that must not be stored",
                        },
                    },
                    {
                        "op": "replace",
                        "path": ["latestTokenUsageInfo"],
                        "value": {"totalTokens": 123},
                    },
                ],
            },
        },
    }


def _discovery(method: str) -> dict:
    return {
        "type": "client-discovery-request",
        "requestId": f"discovery-{method}",
        "request": {
            "type": "request",
            "requestId": f"inner-{method}",
            "sourceClientId": "desktop-client",
            "version": 0,
            "method": method,
            "params": {
                "workspaceRoot": "/Users/sam.zhang/Documents/codex-supervisor",
                "ANTHROPIC_AUTH_TOKEN": "sk-ant-do-not-store",
            },
        },
    }


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(message) for message in messages) + "\n")


def test_capture_summary_sanitizes_values_and_keeps_phase_signal(tmp_path: Path) -> None:
    capture = tmp_path / "normal-turn.jsonl"
    _write_jsonl(capture, [
        _snapshot(),
        _patches(),
        _discovery("ide-context"),
        {
            "type": "request",
            "method": "turn/start",
            "params": {"prompt": "do not store this prompt"},
        },
    ])

    summary = summarize_desktop_ipc_capture(capture, phase="normal_turn")

    assert summary == {
        "phase": "normal_turn",
        "message_count": 4,
        "message_types": ["broadcast", "client-discovery-request", "request"],
        "methods": ["thread-stream-state-changed", "turn/start"],
        "client_discovery_methods": ["ide-context"],
        "stream_event_count": 2,
        "stream_change_types": ["patches", "snapshot"],
        "stream_patch_paths": ["latestTokenUsageInfo", "turns/72/items/49"],
        "desktop_gui_repaint": "unverified",
    }
    serialized = json.dumps(summary)
    assert "sk-ant-do-not-store" not in serialized
    assert "visible GUI text" not in serialized
    assert "do not store this prompt" not in serialized


def test_sanitized_capture_frame_preserves_only_allowed_structure() -> None:
    sanitized_stream = sanitize_desktop_ipc_message(_patches())
    sanitized_discovery = sanitize_desktop_ipc_message(_discovery("ide-context"))
    sanitized_request = sanitize_desktop_ipc_message({
        "type": "request",
        "requestId": "request-1",
        "sourceClientId": "desktop",
        "version": 1,
        "method": "turn/start",
        "params": {"prompt": "do not store this prompt"},
    })

    assert sanitized_stream == {
        "type": "broadcast",
        "method": "thread-stream-state-changed",
        "sourceClientId": "desktop",
        "version": 6,
        "params": {
            "conversationId": THREAD_ID,
            "hostId": "local",
            "change": {
                "type": "patches",
                "patches": [
                    {"path": "turns/72/items/49"},
                    {"path": "latestTokenUsageInfo"},
                ],
            },
        },
    }
    assert sanitized_discovery == {
        "type": "client-discovery-request",
        "requestId": "discovery-ide-context",
        "request": {
            "type": "request",
            "requestId": "inner-ide-context",
            "sourceClientId": "desktop-client",
            "version": 0,
            "method": "ide-context",
            "params": {
                "ANTHROPIC_AUTH_TOKEN": None,
                "workspaceRoot": None,
            },
        },
    }
    assert sanitized_request == {
        "type": "request",
        "requestId": "request-1",
        "sourceClientId": "desktop",
        "version": 1,
        "method": "turn/start",
    }
    serialized = json.dumps([
        sanitized_stream,
        sanitized_discovery,
        sanitized_request,
    ])
    assert "visible GUI text" not in serialized
    assert "sk-ant-do-not-store" not in serialized
    assert "do not store this prompt" not in serialized


def test_cold_start_normal_turn_diff_surfaces_probe_candidates(tmp_path: Path) -> None:
    cold = tmp_path / "cold-start.jsonl"
    normal = tmp_path / "normal-turn.jsonl"
    _write_jsonl(cold, [
        _snapshot(),
        {"type": "request", "method": "thread/hydrate", "params": {"raw": "hide-me"}},
        _discovery("ide-context"),
    ])
    _write_jsonl(normal, [
        _snapshot(),
        _patches(),
        {"type": "request", "method": "turn/start", "params": {"prompt": "hide-me"}},
        {"type": "broadcast", "method": "command/exec/outputDelta", "params": {"delta": "hide-me"}},
        _discovery("ide-context"),
    ])

    diff = diff_desktop_ipc_captures(cold, normal)

    assert diff == {
        "cold_start_phase": "cold_start",
        "normal_turn_phase": "normal_turn",
        "methods_only_in_cold_start": ["thread/hydrate"],
        "methods_only_in_normal_turn": ["command/exec/outputDelta", "turn/start"],
        "client_discovery_only_in_cold_start": [],
        "client_discovery_only_in_normal_turn": [],
        "stream_patch_paths_only_in_cold_start": [],
        "stream_patch_paths_only_in_normal_turn": [
            "latestTokenUsageInfo",
            "turns/72/items/49",
        ],
        "candidate_forced_reload_methods": ["thread/hydrate"],
        "candidate_live_turn_methods": ["turn/start"],
        "recommended_next_step": "probe_candidate_live_turn_method",
        "desktop_gui_repaint": "unverified",
    }
    assert "hide-me" not in json.dumps(diff)
