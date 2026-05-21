"""Ticket 03 cycle 1: hook_http_api + mode_policy (PRD promises P3, P4).

Public-boundary test. POST a Claude Code PermissionRequest sample to
/hook/claude-code with `modes.hook_blocking = shadow`. Two things must hold:

  P3: the hook is normalized + audited
    - `hook_requests` has one row
    - `hook_event` is the *normalized* kind (permission_request), not raw
    - `tool_name` is preserved
    - `payload_json` contains the redacted raw payload

  P4: shadow mode cannot deny or mutate
    - HTTP response.action != "deny"

Forbidden outcomes guarded against:
  - "Hook response blocks in shadow mode"
  - "Permission requests are treated as generic unknown events"
  - "Raw hook payload is dropped"
  - "A secret appears in SQLite"
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from supervisor.config import Config
from supervisor.state import State
from supervisor.target.claude_code import ClaudeCodeAdapter
from supervisor.target.codex import CodexAdapter
from supervisor.hook_server import build_app


FIXTURE = Path(__file__).parent / "fixtures" / "config_claude_code_minimal.yaml"

# Secret embedded in the payload — must NOT appear in hook_requests after redaction.
SECRET_LITERAL = "ghp_abcdefghijklmnopqrstuvwxyz123456"


def _load_cfg(tmp_path, hook_blocking: str) -> Config:
    """Load the base fixture, override modes.hook_blocking."""
    text = FIXTURE.read_text()
    text += f"\nmodes:\n  hook_blocking: {hook_blocking}\n"
    p = tmp_path / f"{hook_blocking}.yaml"
    p.write_text(text)
    return Config.load(str(p))


def _load_cfg_with_model_first(tmp_path, hook_blocking: str) -> Config:
    text = FIXTURE.read_text()
    text = text.replace(
        "  state_db: /tmp/test-supervisor/state.db\n",
        "  state_db: /tmp/test-supervisor/state.db\n"
        "  hook_critique_strategy: model_first\n",
    )
    text += f"\nmodes:\n  hook_blocking: {hook_blocking}\n"
    p = tmp_path / f"model_first_{hook_blocking}.yaml"
    p.write_text(text)
    return Config.load(str(p))


class _FakeHookCritic:
    def __init__(self, verdict: dict):
        self.verdict = verdict
        self.calls: list[dict] = []

    async def critique(self, hook_event, *, raw_payload: dict) -> dict:
        self.calls.append({
            "hook_kind": hook_event.hook_kind,
            "tool_name": hook_event.tool_name,
            "raw_payload": raw_payload,
        })
        return self.verdict


class _FailingHookCritic:
    async def critique(self, hook_event, *, raw_payload: dict) -> dict:
        raise RuntimeError("critic unavailable")


def _load_cfg_shadow(tmp_path) -> Config:
    return _load_cfg(tmp_path, "shadow")


def test_permission_request_in_shadow_mode_is_audited_and_non_blocking(tmp_path):
    """RED initially: /hook/claude-code route doesn't exist, ModesCfg doesn't exist."""
    cfg = _load_cfg_shadow(tmp_path)
    state = State(str(tmp_path / "hooks.db"))
    adapter = ClaudeCodeAdapter()
    app = build_app(cfg, state, target_adapter=adapter)

    client = TestClient(app)

    payload = {
        "hook_event": "PermissionRequest",
        "session_id": "sess-perm-1",
        "tool_name": "Bash",
        "tool_args": {
            "command": (
                f"curl -H 'Authorization: Bearer {SECRET_LITERAL}' "
                f"https://api.example.com"
            ),
        },
        "permission": {"reason": "needs to call external API"},
    }

    r = client.post("/hook/claude-code", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()

    # P4 forbidden: shadow mode cannot deny.
    assert body.get("action") != "deny", (
        f"shadow mode must NOT deny; got {body!r}"
    )

    # P3: every hook call is audited.
    conn = sqlite3.connect(state.db_path)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM hook_requests"))
    assert len(rows) == 1, f"expected exactly one audit row, got {len(rows)}"

    row = rows[0]

    # P3: normalized hook_event (not raw "PermissionRequest").
    assert row["hook_event"] == "permission_request", (
        f"hook_event must be normalized; got {row['hook_event']!r}. "
        "(forbidden: permission requests are treated as generic unknown events)"
    )

    # P3: tool_name preserved through normalization.
    assert row["tool_name"] == "Bash"

    # P4: stored mode matches operating mode at decision time.
    assert row["mode"] == "shadow"

    # P3: raw payload is preserved (not dropped) but redacted.
    stored_payload = json.loads(row["payload_json"])
    assert stored_payload.get("hook_event") == "PermissionRequest", (
        "raw payload must be preserved (just redacted)"
    )

    # P6 / cross-cutting: secret must NOT survive into stored payload.
    assert SECRET_LITERAL not in row["payload_json"], (
        f"raw secret leaked into hook_requests.payload_json"
    )

    # Response must be valid JSON for the hook source — at minimum an action key.
    assert "action" in body


# ----- Cycle 2: deterministic deny in enforce mode -----

DESTRUCTIVE_CASES = [
    pytest.param({"command": "rm -rf ~/important"},           id="rm_rf"),
    pytest.param({"command": "git push --force origin main"}, id="git_push_force"),
    pytest.param({"command": "git push -f origin main"},      id="git_push_f"),
    pytest.param({"command": "rm .env.production"},           id="rm_env"),
    pytest.param({"command": "rm credentials.json"},          id="rm_credentials"),
    pytest.param({"command": "DROP TABLE users;"},            id="drop_table"),
    pytest.param({"command": "mkfs.ext4 /dev/sda1"},          id="mkfs"),
]


@pytest.mark.parametrize("tool_args", DESTRUCTIVE_CASES)
def test_pre_tool_use_destructive_in_enforce_mode_denies(tmp_path, tool_args):
    """RED initially: enforce mode is not yet wired into /hook/claude-code."""
    cfg = _load_cfg(tmp_path, "enforce")
    state = State(str(tmp_path / "enforce.db"))
    adapter = ClaudeCodeAdapter()
    # NOTE: anthropic is deliberately NOT passed. The deterministic path
    # must not require an LLM client.
    app = build_app(cfg, state, target_adapter=adapter)
    client = TestClient(app)

    payload = {
        "hook_event": "PreToolUse",
        "session_id": "sess-enforce-1",
        "tool_name": "Bash",
        "tool_args": tool_args,
    }
    r = client.post("/hook/claude-code", json=payload)
    assert r.status_code == 200
    body = r.json()

    # Deterministic deny.
    assert body.get("action") == "deny", (
        f"enforce mode must deny destructive PreToolUse; got {body!r}"
    )
    assert body.get("reason"), "deny response must include a reason for the user"

    # Audit: hook_requests.response_json stores the deny.
    conn = sqlite3.connect(state.db_path)
    conn.row_factory = sqlite3.Row
    [row] = list(conn.execute("SELECT * FROM hook_requests"))
    stored_response = json.loads(row["response_json"])
    assert stored_response.get("action") == "deny"
    assert row["mode"] == "enforce"


def test_pre_tool_use_safe_command_in_enforce_mode_allows(tmp_path):
    """Enforce mode must NOT deny safe commands — the detector is deterministic
    and narrow, not LLM-based."""
    cfg = _load_cfg(tmp_path, "enforce")
    state = State(str(tmp_path / "safe.db"))
    app = build_app(cfg, state, target_adapter=ClaudeCodeAdapter())
    client = TestClient(app)

    payload = {
        "hook_event": "PreToolUse",
        "session_id": "sess-safe",
        "tool_name": "Bash",
        "tool_args": {"command": "ls -la src/"},
    }
    r = client.post("/hook/claude-code", json=payload)
    assert r.json().get("action") == "allow"


# ----- Cycle 3: advise mode records recommendation, never denies -----

def test_pre_tool_use_destructive_in_advise_mode_records_recommendation_but_allows(tmp_path):
    """RED initially (would have been): advise must not deny but must produce
    an `actions` row so ticket 06's Telegram path can pick it up."""
    cfg = _load_cfg(tmp_path, "advise")
    state = State(str(tmp_path / "advise.db"))
    app = build_app(cfg, state, target_adapter=ClaudeCodeAdapter())
    client = TestClient(app)

    payload = {
        "hook_event": "PreToolUse",
        "session_id": "sess-advise-1",
        "tool_name": "Bash",
        "tool_args": {"command": "rm -rf ~/important"},
    }
    r = client.post("/hook/claude-code", json=payload)
    body = r.json()

    # P4 forbidden: advise must not deny.
    assert body.get("action") != "deny", (
        f"advise mode must NOT deny; got {body!r}"
    )

    # A recommendation row exists in `actions` — this is the seam ticket 06
    # will read from for Telegram delivery.
    conn = sqlite3.connect(state.db_path)
    conn.row_factory = sqlite3.Row
    actions = list(conn.execute(
        "SELECT * FROM actions WHERE action_type='recommend_block'"))
    assert len(actions) == 1, (
        f"advise mode must record one recommend_block action; got {len(actions)}"
    )
    payload_json = json.loads(actions[0]["payload_json"])
    assert "destructive_command" in payload_json.get("reason", ""), (
        "recommendation payload must explain why it was flagged"
    )

    # And a hook_requests audit row with mode=advise must still exist.
    hooks = list(conn.execute("SELECT * FROM hook_requests"))
    assert len(hooks) == 1 and hooks[0]["mode"] == "advise"


def test_pre_tool_use_safe_in_advise_mode_records_no_recommendation(tmp_path):
    """Advise must NOT spam `actions` rows for safe commands."""
    cfg = _load_cfg(tmp_path, "advise")
    state = State(str(tmp_path / "advise_safe.db"))
    app = build_app(cfg, state, target_adapter=ClaudeCodeAdapter())
    client = TestClient(app)

    r = client.post("/hook/claude-code", json={
        "hook_event": "PreToolUse",
        "session_id": "sess-advise-safe",
        "tool_name": "Bash",
        "tool_args": {"command": "ls -la"},
    })
    assert r.json().get("action") == "allow"

    conn = sqlite3.connect(state.db_path)
    n = conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    assert n == 0, f"safe command must not create an actions row; got {n}"


# ----- Cycle 4: malformed payload safe default + audit -----

def test_malformed_json_body_safe_defaults_to_allow_and_audits(tmp_path):
    """RED initially: current handler returns allow but does NOT write the
    audit row. Forbidden outcome guarded: raw hook payload is dropped."""
    cfg = _load_cfg(tmp_path, "shadow")
    state = State(str(tmp_path / "malformed.db"))
    app = build_app(cfg, state, target_adapter=ClaudeCodeAdapter())
    client = TestClient(app)

    # Raw bytes that aren't valid JSON.
    r = client.post(
        "/hook/claude-code",
        content=b"not even close to json {{{",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json().get("action") != "deny"

    conn = sqlite3.connect(state.db_path)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM hook_requests"))
    assert len(rows) == 1, (
        "malformed payload must still produce one audit row "
        "(forbidden: raw hook payload is dropped)"
    )
    assert rows[0]["hook_event"] == "unknown"
    assert rows[0]["mode"] == "shadow"


def test_minimal_payload_with_no_hook_event_normalizes_to_unknown(tmp_path):
    """Valid JSON but missing hook_event — must normalize to 'unknown', not crash."""
    cfg = _load_cfg(tmp_path, "shadow")
    state = State(str(tmp_path / "minimal.db"))
    app = build_app(cfg, state, target_adapter=ClaudeCodeAdapter())
    client = TestClient(app)

    r = client.post("/hook/claude-code", json={"session_id": "sess-min"})
    assert r.status_code == 200
    assert r.json().get("action") != "deny"

    conn = sqlite3.connect(state.db_path)
    conn.row_factory = sqlite3.Row
    [row] = list(conn.execute("SELECT * FROM hook_requests"))
    assert row["hook_event"] == "unknown"


def test_malformed_payload_still_redacts_secrets(tmp_path):
    """Even a poorly-shaped payload must have its secrets scrubbed."""
    cfg = _load_cfg(tmp_path, "shadow")
    state = State(str(tmp_path / "malformed_secret.db"))
    app = build_app(cfg, state, target_adapter=ClaudeCodeAdapter())
    client = TestClient(app)

    secret = "sk-ant-redactme-1234567890"
    r = client.post(
        "/hook/claude-code",
        json={"weird_field": f"ANTHROPIC_API_KEY={secret}"},
    )
    assert r.status_code == 200

    conn = sqlite3.connect(state.db_path)
    [(payload_json,)] = list(conn.execute(
        "SELECT payload_json FROM hook_requests"))
    assert secret not in payload_json, (
        "redaction must still apply for malformed/edge payloads"
    )


# ----- Codex Desktop live boundary -----

def test_codex_hook_endpoint_uses_codex_adapter_and_audits(tmp_path):
    """Codex Desktop posts to /hook/codex, not /hook/claude-code.

    The endpoint must still use the selected target adapter, normalize the raw
    Codex hook name, and write the same hook_requests audit row.
    """
    cfg = _load_cfg(tmp_path, "shadow")
    cfg.target.kind = "codex"
    state = State(str(tmp_path / "codex-hook.db"))
    app = build_app(cfg, state, target_adapter=CodexAdapter())
    client = TestClient(app)

    r = client.post("/hook/codex", json={
        "event": "PermissionRequest",
        "session_id": "codex-session-1",
        "tool_name": "Bash",
        "arguments": {"command": "echo hi"},
    })
    assert r.status_code == 200
    assert r.json().get("action") == "allow"

    conn = sqlite3.connect(state.db_path)
    conn.row_factory = sqlite3.Row
    [row] = list(conn.execute("SELECT * FROM hook_requests"))
    assert row["hook_event"] == "permission_request"
    assert row["tool_name"] == "Bash"
    assert row["mode"] == "shadow"


# ----- Model-first hook critique -----

def test_model_first_hook_critic_can_deny_safe_command_in_enforce(tmp_path):
    """When model_first is enabled, the model verdict is the first judge.

    A safe command that deterministic rules would allow must still be denied
    when the model critic says deny and hook_blocking=enforce.
    """
    cfg = _load_cfg_with_model_first(tmp_path, "enforce")
    cfg.target.kind = "codex"
    state = State(str(tmp_path / "model-first.db"))
    critic = _FakeHookCritic({
        "action": "deny",
        "reason": "model_says_this_violates_the_user_request",
        "confidence": 0.91,
    })
    app = build_app(
        cfg,
        state,
        target_adapter=CodexAdapter(),
        hook_critic=critic,
    )
    client = TestClient(app)

    r = client.post("/hook/codex", json={
        "event": "PreToolUse",
        "session_id": "codex-model-first",
        "tool_name": "Bash",
        "arguments": {"command": "echo perfectly safe"},
    })

    assert r.status_code == 200
    assert r.json()["action"] == "deny"
    assert r.json()["reason"] == "model_says_this_violates_the_user_request"
    assert critic.calls and critic.calls[0]["hook_kind"] == "pre_tool_use"

    conn = sqlite3.connect(state.db_path)
    conn.row_factory = sqlite3.Row
    [row] = list(conn.execute("SELECT * FROM hook_requests"))
    stored_response = json.loads(row["response_json"])
    assert stored_response["model_verdict"]["action"] == "deny"
    assert row["mode"] == "enforce"


@pytest.mark.parametrize("raw_kind", ["PermissionRequest", "PostToolUse", "Stop"])
def test_model_first_sends_non_pre_tool_hooks_to_critic_but_shadow_allows(tmp_path, raw_kind):
    """Everything means every hook kind is sent to the model critic first.

    Shadow mode still cannot block, even if the model recommends deny.
    """
    cfg = _load_cfg_with_model_first(tmp_path, "shadow")
    cfg.target.kind = "codex"
    state = State(str(tmp_path / f"model-first-{raw_kind}.db"))
    critic = _FakeHookCritic({"action": "deny", "reason": "model_warning"})
    app = build_app(
        cfg,
        state,
        target_adapter=CodexAdapter(),
        hook_critic=critic,
    )
    client = TestClient(app)

    r = client.post("/hook/codex", json={
        "event": raw_kind,
        "session_id": f"codex-{raw_kind}",
        "tool_name": "Bash",
        "arguments": {"command": "echo hi"},
    })

    assert r.status_code == 200
    assert r.json()["action"] == "allow"
    assert critic.calls, f"{raw_kind} must still pass through the critic"


def test_model_first_critic_failure_falls_back_to_deterministic_rules(tmp_path):
    """If the SDK/model is unavailable, enforce mode still uses deterministic
    guardrails instead of failing open on known-dangerous commands."""
    cfg = _load_cfg_with_model_first(tmp_path, "enforce")
    cfg.target.kind = "codex"
    state = State(str(tmp_path / "model-first-fallback.db"))
    app = build_app(
        cfg,
        state,
        target_adapter=CodexAdapter(),
        hook_critic=_FailingHookCritic(),
    )
    client = TestClient(app)

    r = client.post("/hook/codex", json={
        "event": "PreToolUse",
        "session_id": "codex-model-fallback",
        "tool_name": "Bash",
        "arguments": {"command": "rm -rf /tmp/codex-supervisor-smoke"},
    })

    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "deny"
    assert body["reason"].startswith("destructive_command:")
