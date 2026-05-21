"""CS15: approved steering can be delivered through Codex Desktop GUI.

Public boundary: codex_desktop_gui_steering
    CodexAdapter.execute_action(TargetAction(kind="inject_steering", ...))

Forbidden outcomes guarded:
  - desktop GUI steering falls back to invisible CLI resume after GUI failure
  - GUI steering is available without explicit host coordinates
  - raw secrets reach the clipboard / GUI helper
"""
from __future__ import annotations

import pytest

from supervisor.target.codex import CodexAdapter
from supervisor.target.types import TargetAction


class _FakeGuiController:
    def __init__(self, result: dict | None = None) -> None:
        self.messages: list[str] = []
        self.result = result or {
            "delivered": True,
            "reason": "desktop_gui_submitted",
            "method": "desktop_gui_submit",
            "desktop_gui_repaint": "verified",
        }

    async def submit_prompt(self, message: str) -> dict:
        self.messages.append(message)
        return dict(self.result)


@pytest.mark.asyncio
async def test_inject_steering_can_use_desktop_gui_transport(monkeypatch):
    controller = _FakeGuiController()

    async def forbidden_cli_resume(*argv, **kwargs):
        raise AssertionError("desktop GUI steering must not call CLI resume")

    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden_cli_resume)
    monkeypatch.setattr(
        "supervisor.target.codex.CodexDesktopGuiController",
        lambda cfg: controller,
        raising=False,
    )

    adapter = CodexAdapter({
        "steering_delivery": "desktop_gui",
        "desktop_gui_session_click_x": 1045,
        "desktop_gui_session_click_y": -795,
        "desktop_gui_input_x": 1747,
        "desktop_gui_input_y": -285,
    })

    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-vela",
        payload={"message": "Please re-anchor to the current Vela issue."},
    ))

    assert result["delivered"] is True
    assert result["reason"] == "desktop_gui_submitted"
    assert result["method"] == "desktop_gui_submit"
    assert result["desktop_gui_repaint"] == "verified"
    assert result["feature"] == "inject_steering"
    assert result["target"] == "codex"
    assert controller.messages == ["Please re-anchor to the current Vela issue."]


@pytest.mark.asyncio
async def test_desktop_gui_steering_redacts_before_controller(monkeypatch):
    controller = _FakeGuiController()

    async def forbidden_cli_resume(*argv, **kwargs):
        raise AssertionError("desktop GUI steering must not call CLI resume")

    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden_cli_resume)
    monkeypatch.setattr(
        "supervisor.target.codex.CodexDesktopGuiController",
        lambda cfg: controller,
        raising=False,
    )
    adapter = CodexAdapter({
        "steering_delivery": "desktop_gui",
        "desktop_gui_session_click_x": 1045,
        "desktop_gui_session_click_y": -795,
        "desktop_gui_input_x": 1747,
        "desktop_gui_input_y": -285,
    })

    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-vela",
        payload={
            "message": "Use ANTHROPIC_API_KEY=sk-ant-supersecret12345 now.",
        },
    ))

    assert result["delivered"] is True
    assert controller.messages
    assert "sk-ant-supersecret12345" not in controller.messages[0]
    assert "[REDACTED]" in controller.messages[0]


@pytest.mark.asyncio
async def test_desktop_gui_steering_requires_explicit_coordinates(monkeypatch):
    async def forbidden_cli_resume(*argv, **kwargs):
        raise AssertionError("missing GUI config must not fall back to CLI resume")

    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden_cli_resume)
    adapter = CodexAdapter({
        "steering_delivery": "desktop_gui",
        "desktop_gui_session_click_x": 1045,
        "desktop_gui_session_click_y": -795,
    })

    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-vela",
        payload={"message": "Please re-anchor."},
    ))

    assert result["delivered"] is False
    assert result["reason"] == "desktop_gui_coordinates_missing"
    assert result["feature"] == "inject_steering"
    assert result["target"] == "codex"


@pytest.mark.asyncio
async def test_desktop_gui_steering_requires_session_selection_coordinates(monkeypatch):
    async def forbidden_cli_resume(*argv, **kwargs):
        raise AssertionError("missing session selection must not fall back to CLI resume")

    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden_cli_resume)
    adapter = CodexAdapter({
        "steering_delivery": "desktop_gui",
        "desktop_gui_input_x": 1747,
        "desktop_gui_input_y": -285,
    })

    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-vela",
        payload={"message": "Please re-anchor."},
    ))

    assert result["delivered"] is False
    assert result["reason"] == "desktop_gui_session_coordinates_missing"
    assert result["feature"] == "inject_steering"
    assert result["target"] == "codex"
