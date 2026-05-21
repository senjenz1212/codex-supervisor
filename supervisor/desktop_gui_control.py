"""Codex Desktop GUI control for approved steering.

This module is intentionally narrow. It submits one already-approved prompt to
the visible Codex Desktop input by using macOS clipboard + CoreGraphics events.
It is not used for passive progress mirroring because submission starts a real
Codex turn.
"""
from __future__ import annotations

import asyncio
import shutil
from typing import Any

from .redaction import redact


class CodexDesktopGuiController:
    """Submit an approved steering prompt through the visible Desktop GUI."""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        self._cfg = dict(cfg or {})

    async def submit_prompt(self, message: str) -> dict[str, Any]:
        safe_message = str(redact(message)).strip()
        if not safe_message:
            return {
                "delivered": False,
                "reason": "empty_gui_message",
                "method": "desktop_gui_submit",
                "desktop_gui_repaint": "failed",
            }
        x, y = self._input_coordinates()
        if x is None or y is None:
            return {
                "delivered": False,
                "reason": "desktop_gui_coordinates_missing",
                "method": "desktop_gui_submit",
                "desktop_gui_repaint": "failed",
            }
        session_x, session_y = self._session_coordinates()
        if session_x is None or session_y is None:
            return {
                "delivered": False,
                "reason": "desktop_gui_session_coordinates_missing",
                "method": "desktop_gui_submit",
                "desktop_gui_repaint": "failed",
            }

        old_clipboard = await self._capture(["pbpaste"])
        try:
            await self._run_with_input(["pbcopy"], safe_message.encode("utf-8"))
            await self._run([self._swift_command(), "-e", self._swift_script(
                session_x, session_y, x, y,
            )])
        except Exception as e:
            return redact({
                "delivered": False,
                "reason": "desktop_gui_submit_failed",
                "method": "desktop_gui_submit",
                "desktop_gui_repaint": "failed",
                "error": str(e),
            })
        finally:
            if old_clipboard is not None:
                try:
                    await self._run_with_input(["pbcopy"], old_clipboard)
                except Exception:
                    pass

        return {
            "delivered": False,
            "reason": "desktop_gui_events_posted_unverified",
            "method": "desktop_gui_submit",
            "desktop_gui_repaint": "unverified",
            "events_posted": True,
        }

    def _input_coordinates(self) -> tuple[int | None, int | None]:
        x = self._cfg.get("desktop_gui_input_x")
        y = self._cfg.get("desktop_gui_input_y")
        try:
            return int(x), int(y)
        except (TypeError, ValueError):
            return None, None

    def _session_coordinates(self) -> tuple[int | None, int | None]:
        x = self._cfg.get("desktop_gui_session_click_x")
        y = self._cfg.get("desktop_gui_session_click_y")
        try:
            return int(x), int(y)
        except (TypeError, ValueError):
            return None, None

    def _swift_command(self) -> str:
        raw = str(self._cfg.get("desktop_gui_swift_command") or "/usr/bin/swift")
        return shutil.which(raw) or raw

    def _submit_delay_ms(self) -> int:
        try:
            return max(0, min(int(self._cfg.get("desktop_gui_submit_delay_ms", 2000)), 10_000))
        except (TypeError, ValueError):
            return 2000

    def _swift_script(
        self,
        session_x: int,
        session_y: int,
        input_x: int,
        input_y: int,
    ) -> str:
        delay_us = self._submit_delay_ms() * 1000
        return (
            "import CoreGraphics; import Foundation; "
            "let src=CGEventSource(stateID:.hidSystemState); "
            "func click(_ x: Double,_ y: Double){"
            "let p=CGPoint(x:x,y:y); "
            "CGEvent(mouseEventSource:src, mouseType:.leftMouseDown, "
            "mouseCursorPosition:p, mouseButton:.left)?.post(tap:.cghidEventTap); "
            "usleep(80000); "
            "CGEvent(mouseEventSource:src, mouseType:.leftMouseUp, "
            "mouseCursorPosition:p, mouseButton:.left)?.post(tap:.cghidEventTap); "
            "usleep(300000)}; "
            "func key(_ code: CGKeyCode, flags: CGEventFlags = []){"
            "let d=CGEvent(keyboardEventSource:src, virtualKey:code, keyDown:true)!; "
            "d.flags=flags; d.post(tap:.cghidEventTap); usleep(50000); "
            "let u=CGEvent(keyboardEventSource:src, virtualKey:code, keyDown:false)!; "
            "u.flags=flags; u.post(tap:.cghidEventTap); usleep(120000)}; "
            f"click({session_x},{session_y}); "
            "usleep(1000000); "
            f"click({input_x},{input_y}); "
            "key(9, flags:.maskCommand); "
            "key(36); "
            f"usleep({delay_us})"
        )

    async def _capture(self, argv: list[str]) -> bytes | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
            if proc.returncode == 0:
                return stdout
        except Exception:
            return None
        return None

    async def _run_with_input(self, argv: list[str], data: bytes) -> None:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(data), timeout=5)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[:500])

    async def _run(self, argv: list[str]) -> None:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[:500])
