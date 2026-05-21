"""Process monitor target-config compatibility."""
from __future__ import annotations

from supervisor.config import Config
from supervisor.process_monitor import ProcessMonitor
from supervisor.state import State


def test_process_monitor_uses_target_codex_process_names(monkeypatch, tmp_path):
    """Modern configs put Codex settings under target.codex, not cfg.codex."""
    cfg = Config(**{
        "target": {
            "kind": "codex",
            "codex": {
                "sessions_root": str(tmp_path / "sessions"),
                "cli_command": "codex",
                "desktop_process_names": ["Codex Desktop"],
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "fake"},
    })
    state = State(str(tmp_path / "monitor.db"))

    class _Proc:
        info = {"name": "Codex Desktop Helper"}

    monkeypatch.setattr("supervisor.process_monitor.psutil.process_iter", lambda attrs: [_Proc()])

    monitor = ProcessMonitor(cfg, state)
    assert monitor.is_codex_running() is True
