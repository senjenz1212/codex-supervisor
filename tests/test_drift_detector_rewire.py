"""Ticket 04 cycle 4: DriftDetector L1 rewired to evaluate_scope (PRD P4, P5).

The live DriftDetector must use the same scope_policy.evaluate_scope function
that replay uses, operating on the scope_contract from the run_snapshot.

Forbidden outcomes guarded against:
  - "Raw Claude Code or Codex payloads are read in drift logic" (uses normalized events)
  - "Protected path writes are treated as benign" (scope_contract has protected_paths)
  - DriftDetector calling live LLM APIs in any test (tripwired)
"""
from __future__ import annotations
import json
import pytest

from supervisor.state import State
from supervisor.target.types import ScopeContract


def _make_state(tmp_path) -> State:
    return State(str(tmp_path / "test.db"))


def _min_config(db_path: str):
    from supervisor.config import Config
    return Config(**{
        "orchestrator": {"run_registry_dir": "/tmp"},
        "supervisor":   {"state_db": db_path},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model":          "claude-haiku-4-5",
            "drift_l4_model":          "claude-opus-4-5",
            "post_run_eval_model":     "claude-haiku-4-5",
            "embedding_model":         "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "fake"},
    })


class _Tripwire:
    def __init__(self, name: str): self._name = name
    def __call__(self, *a, **kw):
        raise AssertionError(f"DriftDetector must not call {self._name}")
    def __getattr__(self, attr):
        raise AssertionError(f"DriftDetector must not access {self._name}.{attr}")


class _FakeEmbeddingData:
    def __init__(self, embedding):
        self.embedding = embedding


class _FakeEmbeddingResponse:
    def __init__(self):
        self.data = [
            _FakeEmbeddingData([1.0, 0.0]),
            _FakeEmbeddingData([1.0, 0.0]),
        ]


class _FakeEmbeddings:
    def __init__(self):
        self.inputs = None

    async def create(self, *, model, input):
        self.inputs = input
        return _FakeEmbeddingResponse()


class _FakeOpenAI:
    def __init__(self):
        self.embeddings = _FakeEmbeddings()


@pytest.mark.asyncio
async def test_drift_detector_l1_classifies_protected_path(tmp_path):
    """L1 verdict must carry structured findings with 'protected' classification.

    Before rewire: L1 only counted violations against flat scope_hints prefixes
    and didn't distinguish protected from out_of_scope.
    After rewire: L1 uses evaluate_scope with the run_snapshot's scope_contract,
    so protected paths get classification='protected' in the verdict output.
    """
    db_path = str(tmp_path / "test.db")
    state = _make_state(tmp_path)
    state.register_run(
        run_id="run-rw",
        session_id="sess-rw",
        rollout_path="/tmp",
        task="Refactor auth",
        scope=ScopeContract(
            allowed_paths=("src/auth/",),
            related_paths=(),
            protected_paths=("src/payments/",),
            never_touch_patterns=(),
        ),
    )
    # Write enough events to pass the min-3 check.
    for i in range(3):
        state.write_event(run_id="run-rw", source="t", kind="message",
                          payload={"text": f"step {i}"})
    # One protected-path write.
    state.write_event(run_id="run-rw", source="t", kind="file_change",
                      payload={"path": "src/payments/charges.py"})

    cfg = _min_config(db_path)
    # Raise threshold high so L2/L3 are never reached (no live LLM needed).
    cfg.drift.l1_scope_violation_threshold = 999

    from supervisor.drift_detector import DriftDetector
    detector = DriftDetector(
        cfg, state,
        anthropic=_Tripwire("anthropic"),
        oai=_Tripwire("openai"),
    )

    run = state.active_runs()[0]
    await detector._check_one(run)

    verdicts = list(state._conn.execute(
        "SELECT * FROM verdicts WHERE run_id='run-rw' AND layer='L1'"
    ).fetchall())
    assert verdicts, "DriftDetector L1 must write a verdict row"

    output = json.loads(verdicts[0]["output_json"])
    findings = output.get("findings", [])
    assert any(f.get("classification") == "protected" for f in findings), (
        "DriftDetector L1 must classify protected-path writes; "
        f"got findings={findings}"
    )


@pytest.mark.asyncio
async def test_drift_detector_l1_never_touch_is_severe(tmp_path):
    """never_touch writes must appear in L1 verdict findings with severity=severe."""
    db_path = str(tmp_path / "test.db")
    state = _make_state(tmp_path)
    state.register_run(
        run_id="run-nt",
        session_id="sess-nt",
        rollout_path="/tmp",
        task="Deploy",
        scope=ScopeContract(
            allowed_paths=("src/",),
            related_paths=(),
            protected_paths=(),
            never_touch_patterns=("**/.env*",),
        ),
    )
    for i in range(3):
        state.write_event(run_id="run-nt", source="t", kind="message",
                          payload={"text": f"step {i}"})
    state.write_event(run_id="run-nt", source="t", kind="file_change",
                      payload={"path": ".env.production"})

    cfg = _min_config(db_path)
    cfg.drift.l1_scope_violation_threshold = 999

    from supervisor.drift_detector import DriftDetector
    detector = DriftDetector(cfg, state,
                             anthropic=_Tripwire("anthropic"),
                             oai=_Tripwire("openai"))
    run = state.active_runs()[0]
    await detector._check_one(run)

    verdicts = list(state._conn.execute(
        "SELECT * FROM verdicts WHERE run_id='run-nt' AND layer='L1'"
    ).fetchall())
    assert verdicts
    output = json.loads(verdicts[0]["output_json"])
    findings = output.get("findings", [])
    assert any(
        f.get("classification") == "never_touch" and f.get("severity") == "severe"
        for f in findings
    ), f"never_touch finding missing or wrong severity; got findings={findings}"


@pytest.mark.asyncio
async def test_drift_detector_l2_uses_intent_summaries_not_raw_messages(tmp_path):
    """L2 should embed derived intent summaries, not noisy raw message/tool logs."""
    db_path = str(tmp_path / "test.db")
    state = _make_state(tmp_path)
    state.register_run(
        run_id="run-l2",
        session_id="sess-l2",
        rollout_path="/tmp",
        task="Refactor auth/login",
        scope=ScopeContract(
            allowed_paths=("src/auth/",),
            related_paths=(),
            protected_paths=(),
            never_touch_patterns=(),
        ),
    )
    state.write_event(run_id="run-l2", source="t", kind="message",
                      payload={"text": "RAW TOOL LOG: cat huge irrelevant diff"})
    state.write_event(run_id="run-l2", source="t", kind="intent_summary",
                      payload={"summary": "Agent is editing billing files."})
    state.write_event(run_id="run-l2", source="t", kind="file_change",
                      payload={"path": "src/billing/charges.py"})

    cfg = _min_config(db_path)
    cfg.drift.l1_scope_violation_threshold = 1
    cfg.drift.l2_similarity_threshold = 0.5

    from supervisor.drift_detector import DriftDetector
    fake_oai = _FakeOpenAI()
    detector = DriftDetector(
        cfg, state,
        anthropic=_Tripwire("anthropic"),
        oai=fake_oai,
    )

    run = state.active_runs()[0]
    await detector._check_one(run)

    embedded = fake_oai.embeddings.inputs
    assert embedded is not None
    assert embedded[0] == "Refactor auth/login"
    assert "Agent is editing billing files." in embedded[1]
    assert "RAW TOOL LOG" not in embedded[1]
