"""Ticket 02 cycle 4: SQLite WAL mode (PRD §8, P5).

Concurrent reads and crash recovery. WAL is set at State.__init__ time;
this test pins the invariant so a future change can't silently regress it.
"""
from __future__ import annotations
from supervisor.state import State


def test_state_uses_wal_journal_mode(tmp_path):
    state = State(str(tmp_path / "wal.db"))
    assert state.journal_mode() == "wal", (
        f"expected journal_mode=wal, got {state.journal_mode()!r}"
    )
