from __future__ import annotations

from pathlib import Path

from supervisor.dual_agent_lead import PlanningArtifact
from supervisor.planning_validator import validate_planning_artifacts


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
