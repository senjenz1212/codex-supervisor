"""Ticket 05 cycle 1: replay_cli (PRD promise P7).

Public-boundary test. Replay from portable fixture files must:

  * load snapshot.json + events.jsonl + model_outputs.json
  * emit the documented output JSON shape
  * NOT call live Telegram, live target adapters, subprocess, Anthropic,
    OpenAI, httpx, or the current config

The output shape is intentionally minimal in this cycle so ticket 04 (drift)
can fill in scope_findings / verdicts / proposed_actions later without
changing the replay boundary.

Forbidden outcomes guarded against:
  - "Replay calls live target agents, live Telegram, or live LLM APIs by default"
  - "Replay reads current config instead of snapshot"
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import httpx
import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "replay"


class _Tripwire:
    """Any access raises. Use to ensure replay doesn't touch a module."""
    def __init__(self, name: str): self._name = name
    def __call__(self, *a, **kw):
        raise AssertionError(f"replay must not call {self._name}")
    def __getattr__(self, name):
        raise AssertionError(f"replay must not access {self._name}.{name}")


def _install_tripwires(monkeypatch):
    # Import optional SDKs BEFORE patching httpx. openai's _DefaultHttpxClient
    # inherits from httpx.Client at module-import time; patching httpx first
    # makes that inheritance resolution hit the tripwire on __mro_entries__.
    sdk_mods: dict[str, object] = {}
    for mod_name in ("anthropic", "openai"):
        try:
            sdk_mods[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    # Network: httpx is the supervisor's HTTP path everywhere.
    monkeypatch.setattr(httpx, "AsyncClient", _Tripwire("httpx.AsyncClient"))
    monkeypatch.setattr(httpx, "Client",      _Tripwire("httpx.Client"))
    monkeypatch.setattr(httpx, "post",        _Tripwire("httpx.post"),  raising=False)
    monkeypatch.setattr(httpx, "get",         _Tripwire("httpx.get"),   raising=False)

    # Subprocess: blocks `codex exec`, `claude --resume`, anything shell-y.
    monkeypatch.setattr(subprocess, "run",    _Tripwire("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen",  _Tripwire("subprocess.Popen"))
    monkeypatch.setattr(subprocess, "call",   _Tripwire("subprocess.call"),
                        raising=False)

    # Optional LLM SDKs — only patch if importable.
    for mod_name, mod in sdk_mods.items():
        for cls_name in ("AsyncAnthropic", "Anthropic", "AsyncOpenAI", "OpenAI"):
            if hasattr(mod, cls_name):
                monkeypatch.setattr(mod, cls_name, _Tripwire(f"{mod_name}.{cls_name}"))


def test_replay_runs_from_fixtures_without_external_services(monkeypatch):
    """RED initially: supervisor.replay doesn't exist."""
    _install_tripwires(monkeypatch)

    from supervisor.replay import replay_from_fixtures

    result = replay_from_fixtures(
        snapshot_path=FIXTURE_DIR / "snapshot.json",
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )

    expected = json.loads((FIXTURE_DIR / "expected.json").read_text())
    assert result == expected, (
        f"replay output drifted from fixture.\n"
        f"got:      {json.dumps(result, indent=2)}\n"
        f"expected: {json.dumps(expected, indent=2)}"
    )


def test_replay_fails_closed_when_model_outputs_missing(monkeypatch, tmp_path):
    """Cycle 2: if the model_outputs fixture is missing, replay must NOT
    silently fall back to a live LLM. It must raise a clear error so the
    operator sees the gap."""
    _install_tripwires(monkeypatch)

    from supervisor.replay import replay_from_fixtures

    bogus = tmp_path / "no_such_outputs.json"
    with pytest.raises(FileNotFoundError, match="model_outputs"):
        replay_from_fixtures(
            snapshot_path=FIXTURE_DIR / "snapshot.json",
            events_path=FIXTURE_DIR / "events.jsonl",
            model_outputs_path=bogus,
        )


def test_replay_uses_modes_from_snapshot_not_defaults(monkeypatch, tmp_path):
    """Cycle 3: alter the snapshot's modes and assert the replay output
    reflects those modes verbatim. This is what stops live config edits from
    rewriting historical decision context."""
    _install_tripwires(monkeypatch)

    # Build an alternate snapshot with modes flipped to enforce.
    base = json.loads((FIXTURE_DIR / "snapshot.json").read_text())
    base["modes"] = {
        "drift_l1_l3": "enforce",
        "drift_l4": "enforce",
        "hook_blocking": "enforce",
        "steering_injection": "enforce",
        "recovery_actions": "ask_user",
    }
    altered = tmp_path / "altered_snapshot.json"
    altered.write_text(json.dumps(base))

    from supervisor.replay import replay_from_fixtures
    result = replay_from_fixtures(
        snapshot_path=altered,
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )
    assert result["modes"] == base["modes"], (
        "replay must echo modes from the snapshot verbatim, "
        "not collapse them to a default"
    )


def test_replay_does_not_read_live_config(monkeypatch, tmp_path):
    """Replay must read modes/scope from the snapshot, NOT from the current
    `~/.codex-supervisor/config.yaml`. Forbidden outcome explicit in PRD."""
    _install_tripwires(monkeypatch)

    # If replay tried to load a live Config, it would either:
    #   (a) fail because the path doesn't exist, or
    #   (b) read whatever is at the user's real path.
    # We catch case (b) by pointing CODEX_SUPERVISOR_CONFIG at a path that does
    # NOT exist; if replay tries to read it, FileNotFoundError surfaces here.
    monkeypatch.setenv("CODEX_SUPERVISOR_CONFIG",
                        str(tmp_path / "nonexistent.yaml"))

    from supervisor.replay import replay_from_fixtures
    # Must complete without touching the env var path.
    result = replay_from_fixtures(
        snapshot_path=FIXTURE_DIR / "snapshot.json",
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )
    assert result["run_id"] == "run_replay_001"
