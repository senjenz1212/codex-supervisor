"""CodexAdapter — compatibility surface preserved for ticket 07.

Ticket 01 proved the adapter boundary. Ticket 07 adds version-probed resume:
  >=0.130  → codex exec resume <session_id> <prompt>   (current)
  <0.130   → codex resume <session_id> <prompt>        (legacy fallback)
  unknown  → current form (safe forward default)
"""
from __future__ import annotations
import asyncio
import shutil
from pathlib import Path
from typing import Any

from .types import HookEvent, TargetAction, TargetHealth
from .codex_app_server import CodexAppServerClient, CodexAppServerError
from supervisor.desktop_gui_control import CodexDesktopGuiController
from supervisor.redaction import redact


_CAPABILITIES: dict[str, bool] = {
    "hooks":            True,
    "permission_hook":  True,   # Codex CLI ≥ 0.130 ships PermissionRequest
    "rollout_tail":     True,
    "inject_steering":  True,
    "desktop_gui_steering": True,
    "append_status_item": True,
    "kill_session":     False,
    "restart":          False,
}

_HOOK_KIND_MAP: dict[str, str] = {
    "PreToolUse":        "pre_tool_use",
    "PostToolUse":       "post_tool_use",
    "PermissionRequest": "permission_request",
    "UserPromptSubmit":  "user_prompt_submit",
    "Stop":              "stop",
    "SessionStart":      "session_start",
    "SessionEnd":        "session_end",
}


class CodexAdapter:
    """Future-compat adapter for Codex. v1 ships as a stub behind the same
    boundary so supervisor core never special-cases the target."""

    kind = "codex"

    def __init__(self, target_cfg: dict[str, Any] | None = None) -> None:
        self._cfg = dict(target_cfg or {})

    async def health(self) -> TargetHealth:
        return TargetHealth(
            state="unknown",
            detail="codex_adapter_stub",
            capabilities=dict(_CAPABILITIES),
        )

    async def normalize_hook(self, raw_payload: dict[str, Any]) -> HookEvent:
        raw_kind = (raw_payload.get("hook_event")
                    or raw_payload.get("event")
                    or raw_payload.get("type")
                    or "unknown")
        hook_kind = _HOOK_KIND_MAP.get(raw_kind, "unknown")
        return HookEvent(
            source_target=self.kind,
            hook_kind=hook_kind,                                 # type: ignore[arg-type]
            session_id=str(raw_payload.get("session_id", "")),
            tool_name=raw_payload.get("tool_name"),
            tool_args=(raw_payload.get("tool_args")
                       or raw_payload.get("arguments")
                       or {}),
            raw_payload=raw_payload,
        )

    async def execute_action(self, action: TargetAction) -> dict[str, Any]:
        if not self.supports_feature(action.kind):
            return {"delivered": False,
                    "reason": "not_supported",
                    "feature": action.kind,
                    "target": self.kind}
        if action.kind == "append_status_item":
            return await self._append_status_item(action)
        if action.kind != "inject_steering":
            return {"delivered": False,
                    "reason": "not_supported",
                    "feature": action.kind,
                    "target": self.kind}
        message = str(action.payload.get("message") or "").strip()
        if not message:
            return {"delivered": False,
                    "reason": "empty_steering_message",
                    "feature": action.kind,
                    "target": self.kind}
        if self._steering_delivery(action) == "desktop_gui":
            return await self._inject_steering_desktop_gui(action, message)
        cli = self._resolve_cli_command()
        version = await self._detect_cli_version(cli)
        argv = self._build_resume_command(
            action.session_id,
            message,
            version,
            cli_command=cli,
            extra_args=self._resume_extra_args(),
        )
        cwd = self._resume_cwd(action.payload.get("cwd"))
        subprocess_kwargs: dict[str, Any] = {}
        if cwd is not None:
            subprocess_kwargs["cwd"] = str(cwd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **subprocess_kwargs,
            )
            communicate_task = asyncio.create_task(proc.communicate())
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(communicate_task),
                    timeout=self._resume_timeout_s(),
                )
                delivered = proc.returncode == 0
                reason = "delivered" if delivered else "resume_failed"
            except asyncio.TimeoutError:
                communicate_task.add_done_callback(_consume_background_task)
                delivered = True
                reason = "resume_command_started"
                stdout, stderr = b"", b""
        except FileNotFoundError:
            return {"delivered": False,
                    "reason": "codex_binary_not_found",
                    "feature": action.kind,
                    "target": self.kind}
        except Exception as e:
            return {"delivered": False,
                    "reason": "resume_error",
                    "error": str(e),
                    "feature": action.kind,
                    "target": self.kind}
        return redact({
            "delivered": delivered,
            "reason": reason,
            "feature": action.kind,
            "target": self.kind,
            "argv": _redacted_argv(argv),
            "cwd": str(cwd) if cwd is not None else None,
            "stdout": stdout.decode("utf-8", errors="replace")[:1000],
            "stderr": stderr.decode("utf-8", errors="replace")[:1000],
        })

    async def _inject_steering_desktop_gui(
        self,
        action: TargetAction,
        message: str,
    ) -> dict[str, Any]:
        gui_cfg = self._desktop_gui_cfg(action)
        if (
            gui_cfg.get("desktop_gui_session_click_x") is None
            or gui_cfg.get("desktop_gui_session_click_y") is None
        ):
            return {
                "delivered": False,
                "reason": "desktop_gui_session_coordinates_missing",
                "feature": action.kind,
                "target": self.kind,
                "method": "desktop_gui_submit",
                "desktop_gui_repaint": "failed",
            }
        if gui_cfg.get("desktop_gui_input_x") is None or gui_cfg.get("desktop_gui_input_y") is None:
            return {
                "delivered": False,
                "reason": "desktop_gui_coordinates_missing",
                "feature": action.kind,
                "target": self.kind,
                "method": "desktop_gui_submit",
                "desktop_gui_repaint": "failed",
            }
        controller = CodexDesktopGuiController(gui_cfg)
        try:
            result = await controller.submit_prompt(str(redact(message)))
        except Exception as e:
            result = {
                "delivered": False,
                "reason": "desktop_gui_submit_failed",
                "error": str(e),
                "method": "desktop_gui_submit",
                "desktop_gui_repaint": "failed",
            }
        return redact({
            **dict(result),
            "feature": action.kind,
            "target": self.kind,
            "session_id": action.session_id,
            "delivery": "desktop_gui",
        })

    async def _append_status_item(self, action: TargetAction) -> dict[str, Any]:
        thread_id = str(action.payload.get("thread_id") or action.session_id).strip()
        message = str(action.payload.get("message") or action.payload.get("text") or "").strip()
        if not thread_id:
            return {"delivered": False,
                    "reason": "missing_thread_id",
                    "feature": action.kind,
                    "target": self.kind}
        if not message:
            return {"delivered": False,
                    "reason": "empty_status_message",
                    "feature": action.kind,
                    "target": self.kind}
        safe_message = str(redact(message))
        if not safe_message.lower().startswith("supervisor status"):
            safe_message = f"Supervisor status: {safe_message}"
        item = {
            "type": "message",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": safe_message,
            }],
        }
        cli = self._resolve_cli_command()
        client = CodexAppServerClient(
            cli_command=cli,
            timeout_s=self._app_server_timeout_s(),
            transport=self._app_server_transport(),
            socket_path=self._app_server_socket_path(),
            load_thread_before_inject=self._app_server_load_thread_before_inject(),
        )
        try:
            response = await client.inject_items(
                thread_id=thread_id,
                items=[item],
            )
        except FileNotFoundError:
            return {"delivered": False,
                    "reason": "codex_binary_not_found",
                    "feature": action.kind,
                    "target": self.kind}
        except (CodexAppServerError, asyncio.TimeoutError, Exception) as e:
            return redact({
                "delivered": False,
                "reason": "app_server_error",
                "error": str(e),
                "feature": action.kind,
                "target": self.kind,
            })
        return redact({
            "delivered": True,
            "reason": "app_server_injected",
            "feature": action.kind,
            "target": self.kind,
            "thread_id": thread_id,
            "method": "thread/inject_items",
            "item_count": 1,
            "desktop_gui_repaint": "unverified",
            "app_server_response": response,
        })

    def supports_feature(self, feature: str) -> bool:
        return _CAPABILITIES.get(feature, False)

    def build_resume_command(
        self,
        session_id: str,
        prompt: str,
        version_str: str | None,
    ) -> list[str]:
        """Return the argv list to resume a Codex session. Never calls subprocess.

        Version gate (ticket 07):
          >=0.130 → ["codex", "exec", "resume", session_id, prompt]
          <0.130  → ["codex", "resume", session_id, prompt]
          None / unparseable → current form (forward-safe default)
        """
        cli = self._cfg.get("cli_command", "codex")
        return self._build_resume_command(
            session_id,
            prompt,
            version_str,
            cli_command=cli,
            extra_args=self._resume_extra_args(),
        )

    @staticmethod
    def _build_resume_command(
        session_id: str,
        prompt: str,
        version_str: str | None,
        *,
        cli_command: str,
        extra_args: list[str] | None = None,
    ) -> list[str]:
        args = list(extra_args or [])
        if _is_current_form(version_str):
            return [cli_command, "exec", "resume", *args, session_id, prompt]
        return [cli_command, "resume", *args, session_id, prompt]

    def _resume_extra_args(self) -> list[str]:
        raw = self._cfg.get("resume_extra_args") or []
        return [str(arg) for arg in raw if str(arg).strip()]

    def _resume_timeout_s(self) -> int:
        try:
            return max(1, int(self._cfg.get("resume_timeout_s", 60)))
        except (TypeError, ValueError):
            return 60

    def _app_server_timeout_s(self) -> int:
        try:
            return max(1, int(self._cfg.get("app_server_timeout_s", 10)))
        except (TypeError, ValueError):
            return 10

    def _app_server_transport(self) -> str:
        raw = str(self._cfg.get("app_server_transport") or "proxy")
        return "stdio" if raw == "stdio" else "proxy"

    def _app_server_socket_path(self) -> str | None:
        raw = self._cfg.get("app_server_socket_path")
        if not raw:
            return None
        return str(Path(str(raw)).expanduser())

    def _app_server_load_thread_before_inject(self) -> bool:
        raw = self._cfg.get("app_server_load_thread_before_inject", True)
        if isinstance(raw, str):
            return raw.strip().lower() not in {"0", "false", "no", "off"}
        return bool(raw)

    def _steering_delivery(self, action: TargetAction) -> str:
        raw = action.payload.get("delivery") or self._cfg.get("steering_delivery") or "resume"
        delivery = str(raw).strip().lower()
        return delivery if delivery in {"resume", "desktop_gui"} else "resume"

    def _desktop_gui_cfg(self, action: TargetAction) -> dict[str, Any]:
        cfg = dict(self._cfg)
        for key in (
            "desktop_gui_input_x",
            "desktop_gui_input_y",
            "desktop_gui_session_click_x",
            "desktop_gui_session_click_y",
            "desktop_gui_swift_command",
            "desktop_gui_submit_delay_ms",
        ):
            if key in action.payload and action.payload.get(key) is not None:
                cfg[key] = action.payload.get(key)
        return cfg

    @staticmethod
    def _resume_cwd(raw: Any) -> Path | None:
        if not isinstance(raw, str) or not raw.strip():
            return None
        path = Path(raw).expanduser()
        if path.exists() and path.is_dir():
            return path.resolve()
        return None

    def _resolve_cli_command(self) -> str:
        cli = str(self._cfg.get("cli_command", "codex"))
        expanded = str(Path(cli).expanduser())
        if "/" in expanded:
            return expanded
        found = shutil.which(expanded)
        if found:
            return found
        for candidate in self._cfg.get("cli_search_paths") or []:
            path = Path(str(candidate)).expanduser()
            if path.exists() and path.is_file():
                return str(path)
        return expanded

    async def _detect_cli_version(self, cli: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                cli,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=2)
        except Exception:
            return None
        text = (stdout or stderr).decode("utf-8", errors="replace").strip()
        for token in text.split():
            if token and token[0].isdigit():
                return token
        return None


def _is_current_form(version_str: str | None) -> bool:
    """True if version_str indicates >= 0.130 (current exec-resume form)."""
    if not version_str:
        return True  # unknown → safe default to current
    try:
        parts = [int(x) for x in version_str.split(".")[:2]]
        major, minor = parts[0], parts[1] if len(parts) > 1 else 0
        return (major, minor) >= (0, 130)
    except (ValueError, IndexError):
        return True  # unparseable → forward-safe default


def _redacted_argv(argv: list[str]) -> list[str]:
    if len(argv) < 2:
        return argv
    return [*argv[:-1], "[STEERING_PROMPT_REDACTED]"]


def _consume_background_task(task: asyncio.Task[tuple[bytes, bytes]]) -> None:
    try:
        task.result()
    except BaseException:
        pass
