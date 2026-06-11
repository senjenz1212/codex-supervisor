from __future__ import annotations

from pathlib import Path

from supervisor.dual_agent_lead import PlanningArtifact
from supervisor.planning_validator import (
    PlanningRubricResult,
    planning_validation_probe,
    validate_planning_artifacts,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"
KINDS = (
    "prd",
    "issues",
    "tdd_plan",
    "grill_findings",
    "implementation_plan",
)


def _artifact_set(*, replace: tuple[str, str] | None = None) -> tuple[PlanningArtifact, ...]:
    artifacts = []
    for kind in KINDS:
        name = "good"
        if replace is not None and kind == replace[0]:
            name = replace[1]
        artifacts.append(PlanningArtifact(
            path=FIXTURE_ROOT / kind / f"{name}.md",
            kind=kind,  # type: ignore[arg-type]
            mutable_by_worker=False,
        ))
    return tuple(artifacts)


def test_planning_validator_fixture_matrix_accepts_good_and_rejects_stub_or_sneaky():
    good = validate_planning_artifacts(
        _artifact_set(),
        required_kinds=KINDS,
        gate="execution",
    )

    assert good.ok, good.to_event_payload(task_id="fixture", gate="execution")
    assert good.checks["PRD-001"].status == "pass"
    assert good.checks["PLAN-004"].status == "pass"

    for kind in KINDS:
        for fixture_name in ("stub", "sneaky"):
            result = validate_planning_artifacts(
                _artifact_set(replace=(kind, fixture_name)),
                required_kinds=KINDS,
                gate="execution",
            )

            assert not result.ok, f"{kind}/{fixture_name} unexpectedly passed"
            failed = {
                check_id
                for check_id, check in result.checks.items()
                if check.status == "fail"
            }
            assert any(check_id.startswith(kind.split("_")[0].upper()[:4]) for check_id in failed) or failed


def test_planning_validator_blocks_unresolved_plan_traceability(tmp_path):
    artifacts = []
    for kind in KINDS:
        source = FIXTURE_ROOT / kind / "good.md"
        target = tmp_path / f"{kind}.md"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        artifacts.append(PlanningArtifact(
            path=target,
            kind=kind,  # type: ignore[arg-type]
            mutable_by_worker=False,
        ))
    plan = tmp_path / "implementation_plan.md"
    plan.write_text(
        (FIXTURE_ROOT / "implementation_plan" / "good.md")
        .read_text(encoding="utf-8")
        .replace(
            "## Steps",
            "- P9 -> test_nonexistent_public_boundary\n\n## Steps",
        ),
        encoding="utf-8",
    )

    result = validate_planning_artifacts(
        tuple(artifacts),
        required_kinds=KINDS,
        gate="implementation_plan",
    )

    assert not result.ok
    assert result.checks["PLAN-004"].status == "fail"
    assert "P9" in result.checks["PLAN-004"].message
    assert "test_nonexistent_public_boundary" in result.checks["PLAN-004"].message


def test_planning_validator_blocks_keyword_stuffed_prd_with_low_semantic_score(tmp_path):
    artifacts = []
    for kind in KINDS:
        source = FIXTURE_ROOT / kind / "good.md"
        target = tmp_path / f"{kind}.md"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        artifacts.append(PlanningArtifact(path=target, kind=kind))  # type: ignore[arg-type]
    prd = tmp_path / "prd.md"
    prd.write_text(
        "\n".join([
            "## Problem Statement",
            "validate verify run parse collect submit poll " * 5,
            "## Solution",
            "validate verify run parse collect submit poll " * 5,
            "## User Stories",
            "validate verify run parse collect submit poll " * 5,
            "## PRD Promise Contracts",
            "P1. validate\nP2. verify\nP3. run",
            "## Implementation Decisions",
            "validate verify run parse collect submit poll " * 5,
            "## Testing Decisions",
            "validate verify run parse collect submit poll " * 5,
            "## Out Of Scope",
            "validate verify run parse collect submit poll " * 5,
        ]),
        encoding="utf-8",
    )

    result = validate_planning_artifacts(
        tuple(artifacts),
        required_kinds=("prd",),
        gate="prd_review",
        rubric_threshold=0.8,
    )

    assert not result.ok
    assert result.checks["RUBRIC-001"].status == "fail"
    assert result.rubric is not None
    assert result.rubric.score < result.rubric.threshold
    probe = planning_validation_probe(result, task_id="fixture")
    assert not probe.ok
    assert probe.reason == "planning_validation_failed"
    assert probe.details["rubric"]["status"] == "blocked"


def test_keyword_stuffed_prd_blocks_at_default_threshold_0_6(tmp_path):
    artifacts = []
    for kind in KINDS:
        source = FIXTURE_ROOT / kind / "good.md"
        target = tmp_path / f"{kind}.md"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        artifacts.append(PlanningArtifact(path=target, kind=kind))  # type: ignore[arg-type]
    prd = tmp_path / "prd.md"
    prd.write_text(
        "\n".join([
            "## Problem Statement",
            "validate verify run parse collect submit poll",
            "## Solution",
            "validate verify run parse collect submit poll",
            "## User Stories",
            "validate verify run parse collect submit poll",
            "## PRD Promise Contracts",
            "P1. validate",
            "## Implementation Decisions",
            "validate verify run parse collect submit poll",
            "## Testing Decisions",
            "validate verify run parse collect submit poll",
            "## Out Of Scope",
            "validate verify run parse collect submit poll",
        ]),
        encoding="utf-8",
    )

    result = validate_planning_artifacts(
        tuple(artifacts),
        required_kinds=("prd",),
        gate="prd_review",
    )

    assert not result.ok
    assert result.rubric is not None
    assert result.rubric.threshold == 0.6
    assert result.checks["PRD-004"].status == "fail"
    assert result.checks["PRD-005"].status == "fail"
    probe = planning_validation_probe(result, task_id="fixture")
    assert not probe.ok
    assert probe.reason == "planning_validation_failed"


def test_validate_planning_artifacts_threshold_zero_is_clamped_at_public_boundary():
    seen_thresholds: list[float] = []

    def low_score_runner(texts, required, threshold):
        seen_thresholds.append(threshold)
        return PlanningRubricResult(
            score=0.1,
            threshold=threshold,
            status="blocked" if 0.1 < threshold else "accepted",
            policy="block",
            reasons=("forced_low_score",),
        )

    result = validate_planning_artifacts(
        _artifact_set(),
        required_kinds=KINDS,
        gate="execution",
        rubric_threshold=0.0,
        rubric_runner=low_score_runner,
    )

    assert seen_thresholds == [0.2]
    assert not result.ok
    assert result.checks["RUBRIC-001"].status == "fail"
    assert result.rubric is not None
    assert result.rubric.threshold == 0.2


def test_planning_rubric_payload_is_replayable_for_good_artifacts():
    result = validate_planning_artifacts(
        _artifact_set(),
        required_kinds=KINDS,
        gate="execution",
    )

    payload = result.to_event_payload(task_id="fixture", gate="execution")

    assert result.ok, payload
    assert payload["rubric"]["schema_version"] == "planning-semantic-rubric/v1"
    assert payload["rubric"]["score"] == round(float(result.rubric.score), 6)
    assert payload["rubric"]["threshold"] == 0.6
    assert payload["rubric"]["status"] == "accepted"
    assert payload["rubric"]["reasons"]
    assert payload["artifact_hashes"] == result.artifact_hashes
    assert set(payload["artifact_hashes"]) == set(KINDS)
    assert all(len(value) == 64 for value in payload["artifact_hashes"].values())
    probe = planning_validation_probe(result, task_id="fixture")
    assert probe.ok
    assert probe.details["artifact_hashes"] == payload["artifact_hashes"]


def test_planning_rubric_unavailable_follows_policy_and_never_silently_passes():
    def unavailable_runner(texts, required, threshold):
        return None

    blocked = validate_planning_artifacts(
        _artifact_set(),
        required_kinds=KINDS,
        gate="execution",
        rubric_runner=unavailable_runner,
        rubric_unavailable_policy="block",
    )
    degraded = validate_planning_artifacts(
        _artifact_set(),
        required_kinds=KINDS,
        gate="execution",
        rubric_runner=unavailable_runner,
        rubric_unavailable_policy="proceed_degraded",
    )
    explicit = validate_planning_artifacts(
        _artifact_set(),
        required_kinds=KINDS,
        gate="execution",
        rubric_runner=lambda texts, required, threshold: PlanningRubricResult(
            score=0.7,
            threshold=threshold,
            status="accepted",
            policy="block",
            reasons=("external_rubric_ok",),
        ),
    )

    assert not blocked.ok
    assert blocked.rubric is not None
    assert blocked.rubric.unavailable is True
    assert blocked.rubric.status == "unavailable_blocked"
    assert degraded.ok
    assert degraded.rubric is not None
    assert degraded.rubric.status == "proceed_degraded"
    assert explicit.ok
