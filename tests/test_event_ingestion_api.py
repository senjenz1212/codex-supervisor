"""Ticket 02 cycle 2: event_ingestion_api (PRD promise P5).

Tail offset persistence is what stops a daemon restart from re-ingesting
events we've already processed. The test: set an offset, close the State,
reopen against the same DB path, verify the offset is still there.

Forbidden outcome guarded against:
  - "Daemon restart duplicates already-ingested events."
"""
from __future__ import annotations
from pathlib import Path

import pytest

from supervisor.state import State


def test_tail_offset_persists_across_state_restart(tmp_path):
    db = str(tmp_path / "offsets.db")
    rollout_path = "/tmp/fake/rollout.jsonl"

    # First State instance: write an offset.
    state1 = State(db)
    state1.set_tail_offset(rollout_path, 42_000)
    assert state1.get_tail_offset(rollout_path) == 42_000

    # Simulate a daemon restart: close, then reopen the same DB.
    del state1
    state2 = State(db)
    assert state2.get_tail_offset(rollout_path) == 42_000, (
        "tail offset must survive State reinitialization "
        "(forbidden: daemon restart duplicates already-ingested events)"
    )


def test_tail_offset_is_zero_for_unknown_path(tmp_path):
    state = State(str(tmp_path / "fresh.db"))
    assert state.get_tail_offset("/nowhere") == 0
