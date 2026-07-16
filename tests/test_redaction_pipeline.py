"""Ticket 02 cycle 3: redaction_pipeline (PRD promise P6).

Every storage surface AND Telegram-bound text must have secrets redacted before
they hit disk or the wire. T4 grill finding: this test deliberately covers all
five surfaces because testing just events would miss the others.

Forbidden outcome guarded against:
  - "A secret appears in SQLite or Telegram text."
"""
from __future__ import annotations
import re
import sqlite3

import pytest

from supervisor import redaction as redaction_module
from supervisor.state import State


SECRET_CASES = [
    # label,           secret_marker_literal,    plaintext_payload_containing_it
    ("anthropic_key",  "sk-ant-veryverysecret",  "ANTHROPIC_API_KEY=sk-ant-veryverysecret"),
    ("openai_key",     "sk-proj-superseekrit",   "OPENAI_API_KEY=sk-proj-superseekrit"),
    ("bearer_token",   "eyJabcdefXYZ.tail",      "Authorization: Bearer eyJabcdefXYZ.tail"),
    ("password_eq",    "hunter22pass",           "DB_PASSWORD=hunter22pass"),
    ("token_eq",       "github-pat-secretvalue", "GITHUB_TOKEN=github-pat-secretvalue"),
    ("cursor_key",     "crsr_1234567890abcdef",  "Cursor SDK error printed crsr_1234567890abcdef"),
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


def test_redaction_does_not_corrupt_identifiers_containing_sk_prefix():
    from supervisor.redaction import redact_for_telegram

    identifier = "task-policy-1"
    assert redact_for_telegram(identifier) == identifier


def test_legacy_redaction_keeps_the_original_embedded_key_semantics():
    from supervisor.redaction import redact_for_telegram, redact_v1, redact_v2

    historical_value = "prefixsk-abcdef"

    assert redact_v1(historical_value) == "prefix[REDACTED_API_KEY]"
    assert redact_v2(historical_value) == historical_value
    assert redact_for_telegram(historical_value) == historical_value


def test_event_payload_object_keys_are_redacted_before_persistence(tmp_path):
    state = State(str(tmp_path / "object-key.db"))
    state.write_event(
        run_id="object-key-redaction",
        source="test",
        kind="event_msg",
        payload={"API_KEY=super-secret-value": "ordinary"},
    )

    dump = _dump_all_rows(state.db_path)
    [event] = state.read_events_since(
        "object-key-redaction",
        after_event_id=0,
        limit=10,
    )

    assert "super-secret-value" not in dump
    assert event["payload"]["API_KEY=[REDACTED]"] == "ordinary"


def test_redaction_disambiguates_object_key_collisions():
    from supervisor.redaction import redact

    payload = {
        "API_KEY=super-secret-value": "secret-key",
        "API_KEY=[REDACTED]": "existing-marker",
    }

    result = redact(payload)

    assert result == {
        "API_KEY=[REDACTED]": "secret-key",
        "API_KEY=[REDACTED_2]": "existing-marker",
    }
    assert redact(payload) == result
    assert "super-secret-value" not in repr(result)


def test_redaction_disambiguates_colliding_secret_keys():
    from supervisor.redaction import redact

    payload = {
        "sk-" + "a" * 20: "first",
        "sk-" + "b" * 20: "second",
        "sk-" + "c" * 20: "third",
    }

    result = redact(payload)

    assert result == {
        "[REDACTED_API_KEY]": "first",
        "[REDACTED_API_KEY_2]": "second",
        "[REDACTED_API_KEY_3]": "third",
    }


def test_historical_ledger_redaction_rules_remain_frozen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    future_patterns = (
        *redaction_module._V1_PATTERNS,
        (re.compile(r"future-secret"), "[REDACTED-FUTURE]"),
    )
    monkeypatch.setattr(
        redaction_module,
        "_PATTERNS",
        future_patterns,
    )
    monkeypatch.setattr(
        redaction_module,
        "_V1_PATTERNS",
        future_patterns,
    )
    monkeypatch.setattr(
        redaction_module,
        "_V2_PATTERNS",
        future_patterns,
    )

    assert redaction_module.redact("future-secret") == "[REDACTED-FUTURE]"
    assert redaction_module.redact_v1("future-secret") == "future-secret"
    assert redaction_module.redact_v2("future-secret") == "future-secret"
    state = State(str(tmp_path / "state.db"))
    state.write_event(
        run_id="frozen-redactor",
        source="test",
        kind="event_msg",
        payload={"text": "future-secret"},
    )
    [event] = state.read_events_since(
        "frozen-redactor",
        after_event_id=0,
        limit=10,
    )

    assert event["payload"]["text"] == "future-secret"
    assert state.verify_event_ledger_structure(
        "frozen-redactor"
    ).valid is True
