"""Ticket 04 cycle 1: L1 scope findings via replay (PRD promises P4, P5, P7).

Public-boundary test. The first slice is L1 only:
  - emit findings for writes outside scope
  - emit SEVERE findings for protected-path or never-touch writes
  - cite the source event_id on every finding
  - benign reads outside scope must NOT count
  - shadow mode → no proposed_actions

Forbidden outcomes guarded against:
  - "Raw Claude Code or Codex payloads are read in drift logic"
    (proven by running through the replay boundary, not against raw fixtures)
  - "Protected path writes are treated as benign"
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import httpx
import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "replay" / "drift_l1"
L4_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "replay" / "drift_l4"


class _Tripwire:
    def __init__(self, name: str): self._name = name
    def __call__(self, *a, **kw):
        raise AssertionError(f"drift L1 must not call {self._name}")
    def __getattr__(self, name):
        raise AssertionError(f"drift L1 must not access {self._name}.{name}")


def _install_tripwires(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _Tripwire("httpx.AsyncClient"))
    monkeypatch.setattr(httpx, "Client",      _Tripwire("httpx.Client"))
    monkeypatch.setattr(subprocess, "run",    _Tripwire("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen",  _Tripwire("subprocess.Popen"))


def test_l1_drift_replay_emits_severity_findings(monkeypatch):
    """RED initially: replay doesn't populate scope_findings yet."""
    _install_tripwires(monkeypatch)

    from supervisor.replay import replay_from_fixtures
    result = replay_from_fixtures(
        snapshot_path=FIXTURE_DIR / "snapshot.json",
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )

    expected = json.loads((FIXTURE_DIR / "expected.json").read_text())

    # Exact match: this fixture is the regression harness for every later
    # drift tuning pass.
    assert result == expected, (
        f"L1 drift replay drifted from fixture.\n"
        f"got:      {json.dumps(result, indent=2)}\n"
        f"expected: {json.dumps(expected, indent=2)}"
    )

    findings = result["scope_findings"]
    # Stronger structural assertions on top of the exact-match — these are the
    # invariants that ticket 04's next cycles must preserve as the schema grows.
    assert findings, "scope_findings must be non-empty for this fixture"
    for f in findings:
        assert "event_id" in f, "every finding must cite an event_id"
        assert f["event_id"] in {11, 12, 13, 14, 15}, (
            "event_id must reference an event in this fixture"
        )

    by_class = {f["classification"]: f for f in findings}
    assert by_class["protected"]["severity"]   == "severe"
    assert by_class["never_touch"]["severity"] == "severe"
    assert by_class["out_of_scope"]["severity"] == "warn"

    # Benign read outside scope must NOT appear.
    assert all(f["event_id"] != 15 for f in findings), (
        "out-of-scope READ must NOT count as drift"
    )
    # Allowed write must NOT appear as a violation.
    assert all(f["event_id"] != 11 for f in findings), (
        "allowed-path write must NOT generate a violation finding"
    )

    # Shadow mode → no proposed_actions in this cycle.
    assert result["proposed_actions"] == [], (
        "shadow mode (drift_l1_l3) must not produce proposed_actions"
    )


# ----- Cycle 2: mode policy maps findings → proposed_actions -----

def _snapshot_with_mode(tmp_path, mode: str) -> Path:
    """Clone the L1 snapshot with drift_l1_l3 overridden."""
    base = json.loads((FIXTURE_DIR / "snapshot.json").read_text())
    base["modes"]["drift_l1_l3"] = mode
    out = tmp_path / f"snapshot_{mode}.json"
    out.write_text(json.dumps(base))
    return out


def test_l1_drift_shadow_mode_proposes_no_actions(monkeypatch, tmp_path):
    """shadow → proposed_actions stays empty regardless of findings."""
    _install_tripwires(monkeypatch)
    from supervisor.replay import replay_from_fixtures

    result = replay_from_fixtures(
        snapshot_path=_snapshot_with_mode(tmp_path, "shadow"),
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )
    assert result["scope_findings"], "fixture should still produce findings"
    assert result["proposed_actions"] == []


def test_l1_drift_advise_mode_proposes_recommend_with_evidence(monkeypatch, tmp_path):
    """advise → exactly one recommend_scope_violation action that cites every
    finding's event_id and inherits max severity."""
    _install_tripwires(monkeypatch)
    from supervisor.replay import replay_from_fixtures

    result = replay_from_fixtures(
        snapshot_path=_snapshot_with_mode(tmp_path, "advise"),
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )
    actions = result["proposed_actions"]
    assert len(actions) == 1, f"expected exactly one action; got {actions}"
    a = actions[0]
    assert a["kind"]     == "recommend_scope_violation"
    assert a["status"]   == "would_recommend"
    assert a["run_id"]   == "run_drift_l1_001"
    assert a["session_id"] == "sess-drift-l1-001"
    # Cites every finding's event_id from this fixture.
    assert set(a["evidence_event_ids"]) == {12, 13, 14}
    # Max severity bubbles up from severe findings.
    assert a["severity"] == "severe"
    # Forbidden in advise: must NOT be a denial/kill/restart shape.
    assert a["kind"] not in ("ask_user", "deny_next_write", "kill", "restart")


def test_l1_drift_enforce_mode_severe_findings_ask_user_never_executes(
        monkeypatch, tmp_path):
    """enforce + severe (protected / never_touch) → ask_user.
    Destructive recovery always asks; the mode policy NEVER emits an
    immediate kill/restart even in enforce."""
    _install_tripwires(monkeypatch)
    from supervisor.replay import replay_from_fixtures

    result = replay_from_fixtures(
        snapshot_path=_snapshot_with_mode(tmp_path, "enforce"),
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )
    actions = result["proposed_actions"]
    assert len(actions) == 1
    a = actions[0]
    assert a["kind"]   == "ask_user"
    assert a["status"] == "would_ask"
    assert a["severity"] == "severe"
    assert a["options"], "ask_user proposal must include user-facing options"
    # Forbidden: immediate destructive actions even in enforce.
    forbidden_kinds = {"kill", "restart", "execute_deny"}
    for action in actions:
        assert action["kind"] not in forbidden_kinds, (
            f"enforce must never emit immediate {action['kind']!r} for severe "
            "findings — destructive recovery always asks"
        )


# ----- Direct unit test: enforce + only-warn findings -----

def test_propose_actions_enforce_warn_only_emits_deny_next_write():
    """When only out-of-scope (warn) findings exist, enforce mode emits
    deny_next_write instead of ask_user. Tested directly to avoid building a
    new full replay fixture for the corner case."""
    from supervisor.mode_policy import propose_actions

    findings = [
        {"event_id": 99, "path": "src/billing/x.py",
         "classification": "out_of_scope", "severity": "warn"},
    ]
    actions = propose_actions(
        findings,
        modes={"drift_l1_l3": "enforce"},
        run_id="run_warn_only",
        session_id="sess-warn",
    )
    assert len(actions) == 1
    a = actions[0]
    assert a["kind"]     == "deny_next_write"
    assert a["status"]   == "would_deny_next_write"
    assert a["severity"] == "warn"
    assert a["evidence_event_ids"] == [99]


def test_propose_actions_no_findings_produces_no_actions():
    """Sanity floor: no findings → no actions regardless of mode."""
    from supervisor.mode_policy import propose_actions
    for mode in ("off", "shadow", "advise", "enforce"):
        assert propose_actions(
            [], modes={"drift_l1_l3": mode},
            run_id="r", session_id="s",
        ) == [], f"mode={mode} with no findings should be empty"


# ----- Cycle 3: L1 verdict schema in replay output -----

def test_l1_drift_replay_verdicts_have_schema(monkeypatch):
    """Cycle 3 RED: replay must emit at least one L1 verdict when findings exist.

    The verdict must carry the full L4-compatible schema fields:
      layer, run_id, classification, confidence, evidence_event_ids,
      recommended_action, user_visible_summary.
    This fixture has severe findings → recommended_action must be ask_user.
    """
    _install_tripwires(monkeypatch)
    from supervisor.replay import replay_from_fixtures

    result = replay_from_fixtures(
        snapshot_path=FIXTURE_DIR / "snapshot.json",
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )
    verdicts = result["verdicts"]
    assert verdicts, "L1 verdicts must be non-empty when findings exist"

    v = verdicts[0]
    assert v["layer"]                == "L1"
    assert v["run_id"]               == "run_drift_l1_001"
    assert v["classification"]       == "scope_violation"
    assert v["confidence"]           == 1.0
    assert isinstance(v["evidence_event_ids"], list)
    assert set(v["evidence_event_ids"]) == {12, 13, 14}, (
        "evidence_event_ids must cite every finding's event_id"
    )
    assert v["recommended_action"]   == "ask_user", (
        "severe findings → recommended_action must be ask_user"
    )
    assert isinstance(v["user_visible_summary"], str) and v["user_visible_summary"]


def test_l1_drift_replay_no_verdicts_when_no_findings(monkeypatch, tmp_path):
    """No findings → no verdicts (verdict list must stay empty, not produce garbage)."""
    _install_tripwires(monkeypatch)

    # Snapshot with scope that would allow everything → no findings.
    base = json.loads((FIXTURE_DIR / "snapshot.json").read_text())
    base["scope_contract"]["allowed_paths"] = [""]  # prefix "" matches everything
    base["scope_contract"]["protected_paths"] = []
    base["scope_contract"]["never_touch_patterns"] = []
    snap = tmp_path / "open_scope.json"
    snap.write_text(json.dumps(base))

    from supervisor.replay import replay_from_fixtures
    result = replay_from_fixtures(
        snapshot_path=snap,
        events_path=FIXTURE_DIR / "events.jsonl",
        model_outputs_path=FIXTURE_DIR / "model_outputs.json",
    )
    assert result["scope_findings"] == []
    assert result["verdicts"]       == []


# ----- Cycle 4: fixture-backed L2/L3/L4 replay verdicts -----

def test_replay_uses_intent_summaries_and_l4_model_fixture(monkeypatch):
    """Replay should accept derived intent-summary events for L2, use the
    refined L3 plan-status enum, and emit an L4 verdict with event citations
    from model_outputs.json without live model calls."""
    _install_tripwires(monkeypatch)
    from supervisor.replay import replay_from_fixtures

    result = replay_from_fixtures(
        snapshot_path=L4_FIXTURE_DIR / "snapshot.json",
        events_path=L4_FIXTURE_DIR / "events.jsonl",
        model_outputs_path=L4_FIXTURE_DIR / "model_outputs.json",
    )

    verdicts = result["verdicts"]
    by_layer = {v["layer"]: v for v in verdicts}
    assert {"L1", "L2", "L3", "L4"}.issubset(by_layer), verdicts

    assert by_layer["L2"]["intent_summary_event_ids"] == [21]
    assert by_layer["L2"]["similarity"] == 0.18

    assert by_layer["L3"]["plan_status"] == "blocked"
    assert by_layer["L3"]["plan_status"] in {
        "on_plan", "adjacent", "blocked", "exploratory", "abandoned",
    }

    l4 = by_layer["L4"]
    assert l4["classification"] == "hard_divergence"
    assert l4["confidence"] == 0.91
    assert l4["evidence_event_ids"] == [21, 22, 23]
    assert l4["recommended_action"] == "ask_user"
    assert l4["user_visible_summary"]


def test_replay_rejects_invalid_l3_status(monkeypatch, tmp_path):
    """An invalid fixture status should fail replay instead of silently
    producing a non-contract L3 verdict."""
    _install_tripwires(monkeypatch)
    model_outputs = json.loads((L4_FIXTURE_DIR / "model_outputs.json").read_text())
    model_outputs["by_phase"]["L3"]["plan_status"] = "unclear"
    bad_model_outputs = tmp_path / "model_outputs_bad_l3.json"
    bad_model_outputs.write_text(json.dumps(model_outputs))

    from supervisor.replay import replay_from_fixtures
    with pytest.raises(ValueError, match="invalid L3 plan_status"):
        replay_from_fixtures(
            snapshot_path=L4_FIXTURE_DIR / "snapshot.json",
            events_path=L4_FIXTURE_DIR / "events.jsonl",
            model_outputs_path=bad_model_outputs,
        )
