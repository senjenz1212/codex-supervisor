from __future__ import annotations

from pathlib import Path

from supervisor.dual_agent_lead import LeadInvocationRequest, invoke_claude_lead
from supervisor.dual_agent_runner import make_replay_runner


FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "dual_agent"
    / "live_lead_no_budget_probe_20260524_01"
)


def test_live_lead_no_budget_fixtures_replay_to_typed_outcomes(tmp_path):
    stdout_fixtures = sorted(FIXTURE_DIR.glob("lead-*.stdout.json"))

    assert len(stdout_fixtures) == 6
    for stdout_fixture in stdout_fixtures:
        result = invoke_claude_lead(
            LeadInvocationRequest(
                task_id="live-lead-no-budget-probe-20260524-01",
                gate="tdd_review",
                instruction="Replay captured live /lead fixture.",
                cwd=tmp_path,
            ),
            runner=make_replay_runner(stdout_fixture),
        )

        assert result.probe.ok, stdout_fixture.name
        assert result.outcome is not None
        assert result.outcome.task_id == "live-lead-no-budget-probe-20260524-01"
        assert result.stdout_bytes > 0
        assert result.cost_usd is None or result.cost_usd >= 0
