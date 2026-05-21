"""Ticket 02 cycle 1: run_registration_api (PRD promise P5).

Public-boundary test. Registering a run writes ONE immutable snapshot. A
subsequent register call with a different scope contract must NOT mutate the
stored snapshot — replay depends on this invariant.

Forbidden outcomes guarded against:
  - "Run snapshot changes after the run has started."
  - "Live config rewrites old scope."
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from supervisor.state import State
from supervisor.target.types import ScopeContract


def test_register_run_writes_immutable_snapshot(tmp_path):
    """RED initially: State has no register_run / run_snapshots semantics."""
    state = State(str(tmp_path / "snap.db"))

    original = ScopeContract(
        allowed_paths=("src/auth/",),
        related_paths=("src/lib/",),
        protected_paths=("src/payments/",),
        never_touch_patterns=("**/secret.txt",),
    )

    state.register_run(
        run_id="run_42",
        session_id="sess_xyz",
        rollout_path="/tmp/fake.jsonl",
        task="Fix the auth bug",
        scope=original,
    )

    snap = state.get_run_snapshot("run_42")
    assert snap is not None
    stored = ScopeContract.from_dict(json.loads(snap["scope_contract_json"]))

    # P5: stored scope matches caller's contract.
    assert "src/auth/" in stored.allowed_paths
    assert "src/payments/" in stored.protected_paths
    # Built-in never-touch patterns must always be present (PRD §7, P5).
    assert "**/secret.txt" in stored.never_touch_patterns
    assert any(p.startswith("**/.env") for p in stored.never_touch_patterns), (
        "built-in never-touch baseline must always be present in stored snapshot"
    )

    # Forbidden outcome: re-registering with a different scope must NOT mutate.
    state.register_run(
        run_id="run_42",
        session_id="sess_xyz",
        rollout_path="/tmp/fake.jsonl",
        task="(different task)",
        scope=ScopeContract(
            allowed_paths=("src/completely/different/",),
            never_touch_patterns=(),
        ),
    )

    snap2 = state.get_run_snapshot("run_42")
    stored2 = ScopeContract.from_dict(json.loads(snap2["scope_contract_json"]))
    assert stored2 == stored, (
        "run snapshot changed after re-registration — forbidden by P5"
    )
