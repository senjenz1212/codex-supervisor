"""Framed Codex Desktop IPC client.

Codex Desktop's Electron IPC socket is not newline-delimited JSON-RPC. It uses
4-byte little-endian length-prefixed JSON frames with `request`, `response`,
and `broadcast` message types. This client is intentionally narrow and safe:
it can initialize, make diagnostic requests, and send cache invalidation
broadcasts. It does not start or steer turns.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable


class CodexDesktopIpcError(RuntimeError):
    """Raised when the Desktop IPC frame protocol cannot complete."""


SocketFactory = Callable[..., Any]
RequestHandler = Callable[[dict[str, Any]], dict[str, Any]]
MUTATING_METHODS = frozenset({
    "turn/start",
    "turn/steer",
    "turn/interrupt",
})


class CodexDesktopIpcClient:
    def __init__(
        self,
        *,
        socket_path: str | None = None,
        timeout_s: int = 3,
        client_type: str = "CODEX_SUPERVISOR",
        socket_factory: SocketFactory | None = None,
        request_handlers: dict[str, RequestHandler] | None = None,
    ) -> None:
        self.socket_path = socket_path or default_socket_path()
        self.timeout_s = max(1, int(timeout_s))
        self.client_type = client_type
        self._socket_factory = socket_factory or socket.socket
        self._request_handlers = dict(request_handlers or {})
        self._socket: Any | None = None
        self._client_id: str | None = None
        self.discovery_requests: list[dict[str, Any]] = []

    def initialize(self) -> dict[str, Any]:
        if self._client_id is not None:
            return {"clientId": self._client_id}
        response = self.request(
            "initialize",
            {"clientType": self.client_type},
            source_client_id="codex-supervisor-probe",
            allow_uninitialized=True,
        )
        if response.get("resultType") != "success":
            raise CodexDesktopIpcError(str(response.get("error") or "initialize failed"))
        result = response.get("result")
        if not isinstance(result, dict) or not result.get("clientId"):
            raise CodexDesktopIpcError("initialize response missing clientId")
        self._client_id = str(result["clientId"])
        return {"clientId": self._client_id}

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        version: int = 1,
        source_client_id: str | None = None,
        allow_uninitialized: bool = False,
        allow_mutating_methods: bool = False,
    ) -> dict[str, Any]:
        if method in MUTATING_METHODS and not allow_mutating_methods:
            raise CodexDesktopIpcError(
                f"refusing mutating Desktop IPC method without explicit opt-in: {method}"
            )
        if not allow_uninitialized and self._client_id is None:
            self.initialize()
        request_id = str(uuid.uuid4())
        self._send({
            "type": "request",
            "requestId": request_id,
            "sourceClientId": source_client_id or self._client_id,
            "version": version,
            "method": method,
            "params": params,
        })
        while True:
            message = self._recv()
            if message.get("type") == "response" and message.get("requestId") == request_id:
                return message

    def broadcast(
        self,
        method: str,
        params: dict[str, Any],
        *,
        version: int = 1,
    ) -> dict[str, Any]:
        if self._client_id is None:
            self.initialize()
        self._send({
            "type": "broadcast",
            "method": method,
            "sourceClientId": self._client_id,
            "version": version,
            "params": params,
        })
        return {
            "delivered": True,
            "reason": "desktop_ipc_broadcast_sent",
            "method": method,
            "desktop_gui_repaint": "unverified",
        }

    def broadcast_query_cache_invalidate(self, query_key: list[Any]) -> dict[str, Any]:
        return self.broadcast("query-cache-invalidate", {"queryKey": query_key})

    def recv_message(self) -> dict[str, Any]:
        """Receive one framed IPC message after initializing the client."""
        if self._client_id is None:
            self.initialize()
        message = self._recv()
        self._maybe_handle_incoming_message(message)
        return message

    def capture_messages(
        self,
        *,
        limit: int,
        methods: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Capture framed IPC messages, optionally filtering by method.

        This is a read-only diagnostic path. It only initializes the IPC client;
        it does not submit requests beyond initialize and never starts turns.
        """
        if limit <= 0:
            return []
        if self._client_id is None:
            self.initialize()
        captured: list[dict[str, Any]] = []
        while len(captured) < limit:
            message = self._recv()
            self._maybe_handle_incoming_message(message)
            method = message.get("method")
            if methods is None or method in methods:
                captured.append(message)
        return captured

    def close(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _connect(self) -> Any:
        if self._socket is not None:
            return self._socket
        sock = self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
        if hasattr(sock, "settimeout"):
            sock.settimeout(float(self.timeout_s))
        try:
            sock.connect(self.socket_path)
        except Exception as e:
            raise CodexDesktopIpcError(f"desktop IPC connect failed: {e}") from e
        self._socket = sock
        return sock

    def _send(self, message: dict[str, Any]) -> None:
        sock = self._connect()
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        frame = struct.pack("<I", len(body)) + body
        try:
            sock.sendall(frame)
        except Exception as e:
            raise CodexDesktopIpcError(f"desktop IPC send failed: {e}") from e

    def _recv(self) -> dict[str, Any]:
        sock = self._connect()
        try:
            header = self._recv_exact(sock, 4)
            size = struct.unpack("<I", header)[0]
            if size <= 0 or size > 256 * 1024 * 1024:
                raise CodexDesktopIpcError(f"invalid desktop IPC frame size: {size}")
            body = self._recv_exact(sock, size)
            message = json.loads(body.decode("utf-8"))
        except CodexDesktopIpcError:
            raise
        except Exception as e:
            raise CodexDesktopIpcError(f"desktop IPC receive failed: {e}") from e
        if not isinstance(message, dict):
            raise CodexDesktopIpcError("desktop IPC frame was not a JSON object")
        return message

    def _maybe_handle_incoming_message(self, message: dict[str, Any]) -> None:
        self._maybe_answer_client_discovery(message)
        self._maybe_answer_forwarded_request(message)

    def _maybe_answer_client_discovery(self, message: dict[str, Any]) -> None:
        if message.get("type") != "client-discovery-request":
            return
        summary = summarize_client_discovery_request(message)
        if summary is not None:
            self.discovery_requests.append(summary)
        request_id = message.get("requestId")
        if request_id is None:
            return
        request = message.get("request")
        method = request.get("method") if isinstance(request, dict) else None
        self._send({
            "type": "client-discovery-response",
            "requestId": request_id,
            "response": {"canHandle": method in self._request_handlers},
        })

    def _maybe_answer_forwarded_request(self, message: dict[str, Any]) -> None:
        if message.get("type") != "request":
            return
        method = message.get("method")
        if not isinstance(method, str):
            return
        handler = self._request_handlers.get(method)
        if handler is None:
            return
        request_id = message.get("requestId")
        if request_id is None:
            return
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        try:
            result = dict(handler(params))
        except Exception as e:
            result = {
                "resultType": "error",
                "error": str(e),
            }
        result_type = str(result.pop("resultType", "success"))
        response: dict[str, Any] = {
            "type": "response",
            "requestId": request_id,
            "resultType": result_type,
            "method": method,
        }
        response.update(result)
        self._send(response)

    @staticmethod
    def _recv_exact(sock: Any, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = sock.recv(size - len(chunks))
            if not chunk:
                raise CodexDesktopIpcError("desktop IPC socket closed")
            chunks.extend(chunk)
        return bytes(chunks)


def default_socket_path() -> str:
    tmpdir = Path(tempfile.gettempdir())
    uid = os.getuid() if hasattr(os, "getuid") else None
    name = f"ipc-{uid}.sock" if uid is not None else "ipc.sock"
    return str(tmpdir / "codex-ipc" / name)


def summarize_thread_stream_state_changed(
    message: dict[str, Any],
) -> dict[str, Any] | None:
    if message.get("type") != "broadcast":
        return None
    if message.get("method") != "thread-stream-state-changed":
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    change = params.get("change")
    if not isinstance(change, dict):
        return None
    change_type = str(change.get("type") or "unknown")
    patches = change.get("patches")
    patch_count = len(patches) if isinstance(patches, list) else 0
    patch_paths = _patch_paths(patches) if isinstance(patches, list) else []
    return {
        "method": "thread-stream-state-changed",
        "conversation_id": params.get("conversationId"),
        "host_id": params.get("hostId"),
        "source_client_id": message.get("sourceClientId"),
        "version": message.get("version"),
        "change_type": change_type,
        "has_conversation_state": isinstance(change.get("conversationState"), dict),
        "patch_count": patch_count,
        "patch_paths": patch_paths,
        "desktop_gui_repaint": "unverified",
    }


def summarize_thread_stream_capture(path: str | Path) -> list[dict[str, Any]]:
    messages = _load_jsonl_messages(path)
    summaries: list[dict[str, Any]] = []
    for message in messages:
        summary = summarize_thread_stream_state_changed(message)
        if summary is not None:
            summaries.append(summary)
    return summaries


def summarize_client_discovery_request(
    message: dict[str, Any],
) -> dict[str, Any] | None:
    if message.get("type") != "client-discovery-request":
        return None
    request = message.get("request")
    if not isinstance(request, dict):
        return None
    params = request.get("params")
    param_keys = sorted(params) if isinstance(params, dict) else []
    return {
        "type": "client-discovery-request",
        "request_id": message.get("requestId"),
        "method": request.get("method"),
        "version": request.get("version"),
        "source_client_id": request.get("sourceClientId"),
        "param_keys": param_keys,
    }


def summarize_client_discovery_capture(path: str | Path) -> list[dict[str, Any]]:
    messages = _load_jsonl_messages(path)
    summaries: list[dict[str, Any]] = []
    for message in messages:
        summary = summarize_client_discovery_request(message)
        if summary is not None:
            summaries.append(summary)
    return summaries


def sanitize_desktop_ipc_message(message: dict[str, Any]) -> dict[str, Any]:
    """Return a value-free IPC frame suitable for CS20 capture files."""
    stream = summarize_thread_stream_state_changed(message)
    if stream is not None:
        change: dict[str, Any] = {"type": stream["change_type"]}
        patch_paths = stream.get("patch_paths") or []
        if patch_paths:
            change["patches"] = [{"path": path} for path in patch_paths]
        elif stream.get("has_conversation_state"):
            change["conversationState"] = {}
        return {
            "type": "broadcast",
            "method": "thread-stream-state-changed",
            "sourceClientId": message.get("sourceClientId"),
            "version": message.get("version"),
            "params": {
                "conversationId": stream.get("conversation_id"),
                "hostId": stream.get("host_id"),
                "change": change,
            },
        }

    discovery = summarize_client_discovery_request(message)
    if discovery is not None:
        request = message.get("request")
        request_dict = request if isinstance(request, dict) else {}
        return {
            "type": "client-discovery-request",
            "requestId": message.get("requestId"),
            "request": {
                "type": request_dict.get("type", "request"),
                "requestId": request_dict.get("requestId"),
                "sourceClientId": discovery.get("source_client_id"),
                "version": discovery.get("version"),
                "method": discovery.get("method"),
                "params": {key: None for key in discovery.get("param_keys", [])},
            },
        }

    sanitized: dict[str, Any] = {}
    for key in ("type", "requestId", "sourceClientId", "version", "method"):
        value = message.get(key)
        if value is not None:
            sanitized[key] = value
    return sanitized


def summarize_desktop_ipc_capture(
    path: str | Path,
    *,
    phase: str,
) -> dict[str, Any]:
    """Summarize a Desktop IPC capture without retaining payload values."""
    messages = list(_load_jsonl_messages(path))
    stream_summaries = [
        summary
        for message in messages
        if (summary := summarize_thread_stream_state_changed(message)) is not None
    ]
    discovery_summaries = [
        summary
        for message in messages
        if (summary := summarize_client_discovery_request(message)) is not None
    ]
    return {
        "phase": phase,
        "message_count": len(messages),
        "message_types": sorted({
            message_type
            for message in messages
            if isinstance((message_type := message.get("type")), str)
        }),
        "methods": sorted({
            method
            for message in messages
            if isinstance((method := message.get("method")), str)
        }),
        "client_discovery_methods": sorted({
            str(summary["method"])
            for summary in discovery_summaries
            if summary.get("method") is not None
        }),
        "stream_event_count": len(stream_summaries),
        "stream_change_types": sorted({
            str(summary["change_type"])
            for summary in stream_summaries
            if summary.get("change_type") is not None
        }),
        "stream_patch_paths": sorted({
            path
            for summary in stream_summaries
            for path in summary.get("patch_paths", [])
            if isinstance(path, str)
        }),
        "desktop_gui_repaint": "unverified",
    }


def diff_desktop_ipc_captures(
    cold_start_path: str | Path,
    normal_turn_path: str | Path,
) -> dict[str, Any]:
    """Diff cold-start and normal-turn captures for CS20 spike planning."""
    cold = summarize_desktop_ipc_capture(cold_start_path, phase="cold_start")
    normal = summarize_desktop_ipc_capture(normal_turn_path, phase="normal_turn")

    methods_only_in_cold = _set_diff(cold["methods"], normal["methods"])
    methods_only_in_normal = _set_diff(normal["methods"], cold["methods"])
    reload_candidates = [
        method for method in methods_only_in_cold
        if _looks_like_forced_reload_method(method)
    ]
    turn_candidates = [
        method for method in methods_only_in_normal
        if _looks_like_live_turn_method(method)
    ]
    if turn_candidates:
        next_step = "probe_candidate_live_turn_method"
    elif reload_candidates:
        next_step = "probe_candidate_forced_reload_method"
    else:
        next_step = "prepare_codex_issue"

    return {
        "cold_start_phase": cold["phase"],
        "normal_turn_phase": normal["phase"],
        "methods_only_in_cold_start": methods_only_in_cold,
        "methods_only_in_normal_turn": methods_only_in_normal,
        "client_discovery_only_in_cold_start": _set_diff(
            cold["client_discovery_methods"],
            normal["client_discovery_methods"],
        ),
        "client_discovery_only_in_normal_turn": _set_diff(
            normal["client_discovery_methods"],
            cold["client_discovery_methods"],
        ),
        "stream_patch_paths_only_in_cold_start": _set_diff(
            cold["stream_patch_paths"],
            normal["stream_patch_paths"],
        ),
        "stream_patch_paths_only_in_normal_turn": _set_diff(
            normal["stream_patch_paths"],
            cold["stream_patch_paths"],
        ),
        "candidate_forced_reload_methods": reload_candidates,
        "candidate_live_turn_methods": turn_candidates,
        "recommended_next_step": next_step,
        "desktop_gui_repaint": "unverified",
    }


def _load_jsonl_messages(path: str | Path) -> Iterable[dict[str, Any]]:
    with open(Path(path).expanduser(), encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            message = json.loads(line)
            if isinstance(message, dict):
                yield message


def _patch_paths(patches: list[Any]) -> list[str]:
    paths: list[str] = []
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        raw_path = patch.get("path")
        if isinstance(raw_path, list):
            paths.append("/".join(str(part) for part in raw_path))
        elif isinstance(raw_path, str):
            paths.append(raw_path)
    return paths


def _set_diff(left: Iterable[str], right: Iterable[str]) -> list[str]:
    return sorted(set(left) - set(right))


def _looks_like_forced_reload_method(method: str) -> bool:
    lowered = method.lower()
    if not lowered.startswith("thread/"):
        return False
    return any(
        term in lowered
        for term in ("hydrate", "load", "reload", "resume", "read", "snapshot", "history")
    )


def _looks_like_live_turn_method(method: str) -> bool:
    lowered = method.lower()
    return (
        lowered.startswith("turn/")
        or "steer" in lowered
        or lowered.endswith("/submit")
        or lowered.endswith("/send")
    )
