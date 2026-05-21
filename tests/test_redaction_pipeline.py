"""Ticket 02 cycle 3: redaction_pipeline (PRD promise P6).

Every storage surface AND Telegram-bound text must have secrets redacted before
they hit disk or the wire. T4 grill finding: this test deliberately covers all
five surfaces because testing just events would miss the others.

Forbidden outcome guarded against:
  - "A secret appears in SQLite or Telegram text."
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

import pytest

from supervisor.state import State


SECRET_CASES = [
    # label,           secret_marker_literal,    plaintext_payload_containing_it
    ("anthropic_key",  "sk-ant-veryverysecret",  "ANTHROPIC_API_KEY=sk-ant-veryverysecret"),
    ("openai_key",     "sk-proj-superseekrit",   "OPENAI_API_KEY=sk-proj-superseekrit"),
    ("bearer_token",   "eyJabcdefXYZ.tail",      "Authorization: Bearer eyJabcdefXYZ.tail"),
    ("password_eq",    "hunter22pass",           "DB_PASSWORD=hunter22pass"),
    ("token_eq",       "github-pat-secretvalue", "GITHUB_TOKEN=github-pat-secretvalue"),
    ("private_pem",    "MIIEvAIBADANBgkqhk1234", "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhk1234\n-----END PRIVATE KEY-----"),
]


def _dump_all_rows(db_path: str) -> str:
    """Return every TEXT column from every row, concatenated. Used to assert
    the literal secret string does NOT appear anywhere in the DB."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    chunks: list[str] = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for row in conn.execute(f"SELECT * FROM {name}"):
            for col in row.keys():
                v = row[col]
                if isinstance(v, str):
                    chunks.append(v)
    conn.close()
    return "\n".join(chunks)


@pytest.mark.parametrize("label,secret,plain", SECRET_CASES,
                         ids=[c[0] for c in SECRET_CASES])
def test_event_payload_is_redacted_before_persistence(tmp_path, label, secret, plain):
    state = State(str(tmp_path / f"events_{label}.db"))
    state.upsert_run(run_id="r1", session_id="s1", rollout_path="/x",
                     task="t", scope_hints=[])
    state.write_event(run_id="r1", source="rollout", kind="message",
                      payload={"text": plain, "other": "ok"})
    dump = _dump_all_rows(state.db_path)
    assert secret not in dump, (
        f"raw secret {label!r} leaked into events table; "
        "redaction_pipeline must scrub before persistence"
    )


@pytest.mark.parametrize("label,secret,plain", SECRET_CASES,
                         ids=[c[0] for c in SECRET_CASES])
def test_hook_request_payload_is_redacted(tmp_path, label, secret, plain):
    state = State(str(tmp_path / f"hooks_{label}.db"))
    state.write_hook_request(
        run_id=None, hook_event="PreToolUse", tool_name="shell",
        payload={"command": plain},
        response={"action": "allow"},
        latency_ms=12, mode="shadow",
    )
    dump = _dump_all_rows(state.db_path)
    assert secret not in dump


@pytest.mark.parametrize("label,secret,plain", SECRET_CASES,
                         ids=[c[0] for c in SECRET_CASES])
def test_verdict_output_is_redacted(tmp_path, label, secret, plain):
    state = State(str(tmp_path / f"verdicts_{label}.db"))
    state.write_verdict(
        run_id="r1", phase="realtime", layer=None,
        model="claude-haiku-4-5-20251001",
        output={"reason": f"saw {plain} in command"},
        latency_ms=100, mode="shadow",
    )
    dump = _dump_all_rows(state.db_path)
    assert secret not in dump


@pytest.mark.parametrize("label,secret,plain", SECRET_CASES,
                         ids=[c[0] for c in SECRET_CASES])
def test_action_payload_is_redacted(tmp_path, label, secret, plain):
    state = State(str(tmp_path / f"actions_{label}.db"))
    state.record_action(
        run_id="r1", action_type="inject_steering", requested_by="test",
        payload={"message": plain},
    )
    dump = _dump_all_rows(state.db_path)
    assert secret not in dump


@pytest.mark.parametrize("label,secret,plain", SECRET_CASES,
                         ids=[c[0] for c in SECRET_CASES])
def test_telegram_bound_text_is_redacted(label, secret, plain):
    """Telegram text is its own redaction surface — proves T4 coverage."""
    from supervisor.redaction import redact_for_telegram
    out = redact_for_telegram(plain)
    assert secret not in out, (
        f"raw secret {label!r} leaked through redact_for_telegram"
    )


def test_redaction_leaves_a_marker():
    """Redaction must REPLACE secrets, not just delete the surrounding text."""
    from supervisor.redaction import redact_for_telegram
    out = redact_for_telegram("ANTHROPIC_API_KEY=sk-ant-12345")
    assert "[REDACTED" in out, "redaction must leave a marker so reviewers see what happened"
