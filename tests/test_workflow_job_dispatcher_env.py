from __future__ import annotations

import os
from pathlib import Path

from supervisor.workflow_job_dispatcher import load_dispatcher_env


def test_dispatcher_loads_secrets_env_like_the_shell_cli(tmp_path, monkeypatch):
    # r-2026-06-11: the launchd-detached dispatcher previously loaded no env
    # at all, so spawned claude leads hung silently without credentials
    # (vela2 task B execution: zero stdout for the full 5400s timeout).
    monkeypatch.delenv("DISPATCHER_ENV_CANARY", raising=False)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("DISPATCHER_ENV_CANARY=loaded-ok\n", encoding="utf-8")
    codex_config = tmp_path / "config.toml"
    codex_config.write_text("", encoding="utf-8")

    loaded = load_dispatcher_env(
        secrets_path=secrets,
        codex_config_path=codex_config,
    )

    assert loaded["DISPATCHER_ENV_CANARY"] == "loaded-ok"
    assert os.environ["DISPATCHER_ENV_CANARY"] == "loaded-ok"


def test_dispatcher_env_opt_outs_load_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPATCHER_ENV_CANARY_2", raising=False)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("DISPATCHER_ENV_CANARY_2=should-not-load\n", encoding="utf-8")
    codex_config = tmp_path / "config.toml"
    codex_config.write_text(
        "[mcp_servers.codex_supervisor.env]\nDISPATCHER_ENV_CANARY_2 = \"nope\"\n",
        encoding="utf-8",
    )

    loaded = load_dispatcher_env(
        secrets_path=secrets,
        codex_config_path=codex_config,
        skip_secrets=True,
        skip_codex_mcp_env=True,
    )

    assert loaded == {}
    assert "DISPATCHER_ENV_CANARY_2" not in os.environ
