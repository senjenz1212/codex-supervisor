"""Small Codex app-server JSON-RPC client.

The app-server speaks newline-delimited JSON-RPC over stdio when launched with
`codex app-server --listen stdio://`. This client is intentionally narrow: it
only implements the passive thread-history append path used by CS10.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


class CodexAppServerError(RuntimeError):
    """Raised when the app-server protocol cannot complete."""


class CodexAppServerClient:
    def __init__(
        self,
        *,
        cli_command: str,
        timeout_s: int = 10,
        transport: str = "proxy",
        socket_path: str | None = None,
        load_thread_before_inject: bool = True,
    ) -> None:
        self.cli_command = cli_command
        self.timeout_s = max(1, int(timeout_s))
        self.transport = transport
        self.socket_path = socket_path
        self.load_thread_before_inject = load_thread_before_inject
        self._next_id = 1

    async def inject_items(self, *, thread_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            *self._argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=30_000_000,
        )
        try:
            initialize = await self._request(proc, "initialize", {
                "clientInfo": {
                    "name": "codex-supervisor",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            })
            resumed = None
            if self.load_thread_before_inject:
                resumed = await self._request(proc, "thread/resume", {
                    "threadId": thread_id,
                })
            injected = await self._request(proc, "thread/inject_items", {
                "threadId": thread_id,
                "items": items,
            })
            return {
                "initialize": initialize,
                "thread_resume": _summarize_resume_result(resumed),
                "inject_items": injected,
            }
        finally:
            await self._close(proc)

    def _argv(self) -> list[str]:
        if self.transport == "stdio":
            return [self.cli_command, "app-server", "--listen", "stdio://"]
        argv = [self.cli_command, "app-server", "proxy"]
        if self.socket_path:
            argv.extend(["--sock", self.socket_path])
        return argv

    async def _request(self, proc: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        if proc.stdin is None or proc.stdout is None:
            raise CodexAppServerError("app-server missing stdio pipes")
        proc.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
        await proc.stdin.drain()

        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=self.timeout_s)
            except asyncio.TimeoutError as e:
                raise CodexAppServerError(
                    f"app-server timed out waiting for {method} response"
                ) from e
            if not line:
                stderr = await _read_stderr_snippet(proc)
                detail = f"app-server closed before {method} response"
                if stderr:
                    detail = f"{detail}: {stderr}"
                raise CodexAppServerError(detail)
            try:
                message = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as e:
                raise CodexAppServerError(f"invalid app-server JSON: {e}") from e
            if message.get("id") != request_id:
                # Notifications and responses for other requests may be
                # interleaved. The stdio connection is private, so just skip
                # until the response for the active request arrives.
                continue
            if "error" in message:
                raise CodexAppServerError(str(message["error"]))
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    async def _close(self, proc: Any) -> None:
        stdin = getattr(proc, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()
            except Exception:
                pass
        if getattr(proc, "returncode", None) is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
            except Exception:
                kill = getattr(proc, "kill", None)
                if callable(kill):
                    try:
                        kill()
                    except Exception:
                        pass


async def _read_stderr_snippet(proc: Any) -> str:
    stderr = getattr(proc, "stderr", None)
    read = getattr(stderr, "read", None)
    if not callable(read):
        return ""
    try:
        data = await asyncio.wait_for(read(2000), timeout=0.2)
    except Exception:
        return ""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def _summarize_resume_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    thread = result.get("thread") if isinstance(result.get("thread"), dict) else None
    if thread is None:
        return {"loaded": True}
    return {
        "loaded": True,
        "thread_id": thread.get("id") or thread.get("sessionId"),
        "status": thread.get("status"),
        "name": thread.get("name"),
    }
