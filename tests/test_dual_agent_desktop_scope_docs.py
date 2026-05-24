from __future__ import annotations

from pathlib import Path


def test_dual_agent_skill_uses_desktop_chat_when_telegram_is_absent():
    text = Path("skills/dual-agent-gate.md").read_text()

    assert "telegram_disabled" in text
    assert "Codex Desktop chat" in text
    assert "Do not call `poll_resume_signal`" in text
    assert "no-Telegram path" in text
    assert "re-invoke `start_dual_agent_gate`" in text
    assert "read_gate_transcript" in text
    assert "Before advancing or summarizing" in text
    assert "interactions.md" in text
    assert "Codex/Claude dialogue" in text
    assert "`prd-to-tdd` workflow" in text
    assert "`grill-with-docs` gates" in text
    assert "planning_artifacts" in text
    assert "artifact_policy=\"strict\"" in text
    assert "user_facing=True" in text
    assert "required_artifacts_missing" in text
    assert "accepted `prd_review`" in text
    assert "`issues_review`, and `tdd_review`" in text
    assert "accepted `execution`" in text
    assert "gate_prerequisites_missing" in text
    assert "export_gate_artifacts" in text
    assert "docs/dual-agent/<task_id>/" in text
    assert "`--tools default`" in text
    assert "`--permission-mode bypassPermissions`" in text
    assert "screenshots.md" in text
    assert "Browser or Computer Use" in text
    assert "`source` must be `computer_use` or `browser`" in text
    assert "`visual_validation`" in text
    assert "code diff and test output" in text
    assert "Do not accept a user-facing change on code/tests alone" in text


def test_desktop_probe_doc_covers_g1_g2_g3_and_corrects_execute_flag():
    text = Path("docs/testing/codex-desktop-dual-agent-probes.md").read_text()

    assert "## G-1: Desktop Loads Supervisor MCP" in text
    assert "## G-2: Desktop Runs A Real Claude `/lead` Gate" in text
    assert "## G-3: Desktop Drives Three Dialogue Rounds" in text
    assert "`start_dual_agent_gate` has no `execute=true` flag" in text
    assert "poll_resume_signal` is not called unless a Telegram callback was actually" in text
    assert "read_gate_transcript" in text
    assert "clean Codex/Claude" in text
    assert "dialogue, not just the final outcome" in text
    assert "sqlite3 ~/.codex-supervisor/state.db" in text


def test_new_chat_how_to_covers_dual_agent_handoff_flow():
    text = Path("docs/how-to/dual-agent-from-new-chat.md").read_text()

    assert "codex mcp add codex_supervisor" in text
    assert "uv --directory /Users/sam.zhang/Documents/codex-supervisor" in text
    assert "codex-supervisor-mcp --config ~/.codex-supervisor/config.yaml" in text
    assert "read_gate_transcript" in text
    assert "read_outcome" in text
    assert "docs/dual-agent/<TASK_ID>/interactions.md" in text
    assert "prd_review" in text
    assert "issues_review" in text
    assert "tdd_review" in text
    assert "implementation_plan" in text
    assert "execution" in text
    assert "outcome_review" in text
    assert 'artifact_policy="strict"' in text
    assert "planning_artifacts" in text
    assert "mutable_by_worker=false" in text
    assert "gate_prerequisites_missing" in text
    assert "required_artifacts_missing" in text
    assert "user_facing=True" in text
    assert '"source": "computer_use"' in text
    assert '"status": "passed"' in text
    assert "passed visual validation status" in text
    assert "poll_resume_signal" in text
