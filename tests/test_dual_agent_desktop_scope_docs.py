from __future__ import annotations

from pathlib import Path


def test_dual_agent_skill_uses_desktop_chat_when_telegram_is_absent():
    text = Path("skills/dual-agent-gate.md").read_text()

    assert "telegram_disabled" in text
    assert "Codex Desktop chat" in text
    assert "Do not call `poll_resume_signal`" in text
    assert "no-Telegram path" in text
    assert "re-invoke `start_dual_agent_gate`" in text


def test_desktop_probe_doc_covers_g1_g2_g3_and_corrects_execute_flag():
    text = Path("docs/testing/codex-desktop-dual-agent-probes.md").read_text()

    assert "## G-1: Desktop Loads Supervisor MCP" in text
    assert "## G-2: Desktop Runs A Real Claude `/lead` Gate" in text
    assert "## G-3: Desktop Drives Three Dialogue Rounds" in text
    assert "`start_dual_agent_gate` has no `execute=true` flag" in text
    assert "poll_resume_signal` is not called unless a Telegram callback was actually" in text
    assert "sqlite3 ~/.codex-supervisor/state.db" in text
