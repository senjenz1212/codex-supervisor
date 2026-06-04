from pathlib import Path


ADR_PATH = Path("docs/adr/0004-durable-execution-engine-decision.md")


def test_adr_durable_execution_engine_decision_contains_required_sections() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")

    for option in (
        "Option A: Keep Hand-Rolled",
        "Option B: Temporal",
        "Option C: Restate",
        "Option D: DBOS-Style Postgres Library",
    ):
        assert option in text

    for criterion in (
        "New Operational Surface",
        "Net Code Removed Vs Added",
        "Exactly-Once Submit Guarantee Strength",
        "100-Agent Throughput",
        "Wider-Stack Fit",
        "Migration Blast Radius",
    ):
        assert criterion in text

    for required_section in (
        "Spike Result",
        "What Temporal Would Replace",
        "What Stays Hand-Rolled",
        "Migration Cost",
        "No Default Runtime Change",
        "Recommendation",
    ):
        assert required_section in text

    assert "engine: hand_rolled" in text
    assert "temporal_spike_enabled: false" in text
