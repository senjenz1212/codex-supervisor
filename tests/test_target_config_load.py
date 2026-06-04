"""Ticket 01 RED → GREEN test for `target_config_load` (PRD promises P1, P2).

Public-boundary test. The first behavior we prove is: loading a config with
`target.kind: claude_code` and no Codex section gives us a working Claude Code
adapter, and the legacy v0.2 config with `codex:` still selects Codex via the
adapter boundary.

Forbidden outcomes guarded against:
  - "Codex-specific commands run when target.kind is claude_code"
  - "startup fails because Codex config is missing"
"""
from __future__ import annotations
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "config_claude_code_minimal.yaml"


def test_target_config_load_selects_claude_code_without_codex():
    """RED initially: cfg.target, cfg.codex-is-None, and supervisor.target don't exist yet."""
    from supervisor.config import Config

    cfg = Config.load(str(FIXTURE))

    # P1: Claude Code is the configured target.
    assert cfg.target.kind == "claude_code"

    # P1: codex section is optional. Loading must NOT require it.
    assert cfg.codex is None, (
        "codex section must be optional when target.kind is claude_code "
        "(forbidden outcome: startup fails because Codex config is missing)"
    )

    # P1: adapter selection works without importing Codex-only modules.
    from supervisor.target.factory import build_target_adapter
    adapter = build_target_adapter(cfg)
    assert adapter.kind == "claude_code"
    assert adapter.__class__.__name__ == "ClaudeCodeAdapter"


def test_agentic_lead_defaults_to_allowed_with_three_subagents():
    from supervisor.config import Config

    cfg = Config.load(str(FIXTURE))

    assert cfg.agentic_lead.policy == "allowed"
    assert cfg.agentic_lead.min_subagents == 3


def test_legacy_codex_config_still_selects_codex(tmp_path):
    """Back-compat: an older config that has `codex:` and no `target:` still loads
    and selects the Codex adapter."""
    legacy_yaml = tmp_path / "legacy.yaml"
    legacy_yaml.write_text(
        FIXTURE.read_text()
        .replace("target:\n  kind: claude_code\n\n", "")
        + "\ncodex:\n  sessions_root: /tmp/test/codex-sessions\n"
          "  desktop_process_names: ['Codex']\n"
    )

    from supervisor.config import Config
    from supervisor.target.factory import build_target_adapter

    cfg = Config.load(str(legacy_yaml))
    assert cfg.target.kind == "codex", (
        "a legacy config with a codex: section must infer target.kind=codex"
    )
    adapter = build_target_adapter(cfg)
    assert adapter.kind == "codex"
    assert adapter.__class__.__name__ == "CodexAdapter"


def test_codex_target_defaults_resume_extra_args_for_launchd(tmp_path):
    """Codex steering runs from the daemon cwd, not the user's trusted project
    cwd, so target.codex configs need the trust-bypass resume flag by default.
    """
    p = tmp_path / "codex-target.yaml"
    p.write_text(
        FIXTURE.read_text().replace(
            "target:\n  kind: claude_code\n\n",
            "target:\n"
            "  kind: codex\n"
            "  codex:\n"
            "    sessions_root: /tmp/codex-sessions\n"
            "    cli_command: codex\n\n",
        )
    )

    from supervisor.config import Config

    cfg = Config.load(str(p))
    assert cfg.target is not None
    assert cfg.target.codex is not None
    assert "--skip-git-repo-check" in cfg.target.codex.resume_extra_args
    assert cfg.target.codex.resume_timeout_s >= 60


def test_unity_litellm_model_env_fields_are_loaded(monkeypatch, tmp_path):
    """Supervisor model config must support Unity LiteLLM's Anthropic and
    OpenAI-compatible gateway env vars."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://uai-litellm.internal.unity.com")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret-anthropic")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://uai-litellm.internal.unity.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-openai")

    p = tmp_path / "unity-litellm.yaml"
    p.write_text(FIXTURE.read_text().replace(
        "models:\n",
        "models:\n"
        "  anthropic_base_url: ${ANTHROPIC_BASE_URL}\n"
        "  anthropic_auth_token: ${ANTHROPIC_AUTH_TOKEN}\n"
        "  openai_base_url: ${OPENAI_BASE_URL}\n"
        "  openai_api_key: ${OPENAI_API_KEY}\n"
    ))

    from supervisor.config import Config
    cfg = Config.load(str(p))
    assert cfg.models.anthropic_base_url == "https://uai-litellm.internal.unity.com"
    assert cfg.models.anthropic_auth_token == "secret-anthropic"
    assert cfg.models.openai_base_url == "https://uai-litellm.internal.unity.com/v1"
    assert cfg.models.openai_api_key == "secret-openai"


def test_yaml_bare_off_mode_is_normalized_to_string_off(tmp_path):
    """YAML parses bare `off` as boolean false on this stack.

    The live launchd daemon should not fail startup because an operator used
    natural YAML spelling for a mode value.
    """
    p = tmp_path / "bare-off.yaml"
    p.write_text(
        FIXTURE.read_text()
        + "\nmodes:\n"
          "  desktop_status_sync: off\n"
          "  recovery_actions: off\n"
    )

    from supervisor.config import Config

    cfg = Config.load(str(p))
    assert cfg.modes.desktop_status_sync == "off"
    assert cfg.modes.recovery_actions == "off"
