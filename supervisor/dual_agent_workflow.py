"""Supervisor-owned lifecycle helpers for dual-agent workflows."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dual_agent import Outcome, ProbeResult
from .dual_agent_artifacts import default_dual_agent_artifact_dir
from .state import State


WORKFLOW_GATES: tuple[str, ...] = (
    "prd_review",
    "issues_review",
    "tdd_review",
    "implementation_plan",
    "execution",
    "outcome_review",
)

SOURCE_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("prd", "source/prd.md", "PRD"),
    ("grill_findings", "source/grill-findings.md", "Grill Findings"),
    ("issues", "source/issues.md", "Issues"),
    ("tdd_plan", "source/tdd.md", "TDD Plan"),
    ("implementation_plan", "source/implementation-plan.md", "Implementation Plan"),
)

MANDATORY_ARTIFACTS: tuple[str, ...] = (
    "source/prd.md",
    "source/grill-findings.md",
    "source/issues.md",
    "source/tdd.md",
    "source/implementation-plan.md",
    "interactions.md",
    "outcome-review.md",
    "transcript.md",
)

VISUAL_EVIDENCE_TERMS: tuple[str, ...] = (
    "browser",
    "calendar",
    "computer use",
    "end-to-end",
    "google calendar",
    "live provider",
    "screenshot",
    "slack",
    "vela",
    "visual",
)


@dataclass(frozen=True)
class SourceArtifactSet:
    output_dir: Path
    planning_artifacts: tuple[dict[str, Any], ...]
    files: tuple[Path, ...]


def ensure_workflow_source_artifacts(
    *,
    cwd: str | Path,
    task_id: str,
    intent: str,
) -> SourceArtifactSet:
    """Create missing source docs without overwriting stronger operator-authored docs."""
    output_dir = default_dual_agent_artifact_dir(cwd, task_id)
    source_dir = output_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    planning: list[dict[str, Any]] = []
    for kind, relative_path, title in SOURCE_ARTIFACTS:
        path = output_dir / relative_path
        if not path.exists():
            path.write_text(_seed_artifact_text(title=title, intent=intent), encoding="utf-8")
        files.append(path)
        planning.append({
            "path": str(path),
            "kind": kind,
            "mutable_by_worker": False,
        })
    return SourceArtifactSet(
        output_dir=output_dir,
        planning_artifacts=tuple(planning),
        files=tuple(files),
    )


def mandatory_artifact_status(*, cwd: str | Path, task_id: str) -> dict[str, Any]:
    output_dir = default_dual_agent_artifact_dir(cwd, task_id)
    missing = [
        relative
        for relative in MANDATORY_ARTIFACTS
        if not (output_dir / relative).exists()
    ]
    return {
        "status": "ok" if not missing else "blocked",
        "output_dir": str(output_dir),
        "required": list(MANDATORY_ARTIFACTS),
        "missing": missing,
    }


def workflow_visual_evidence_policy(
    *,
    intent: str,
    task_id: str,
    user_facing: bool,
    planning_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Decide whether outcome review must include Browser/Computer Use evidence."""
    matched_terms = set(_matched_visual_terms(intent))
    artifact_matches: list[dict[str, str]] = []
    for artifact in planning_artifacts:
        path_value = artifact.get("path")
        if not path_value:
            continue
        path = Path(str(path_value)).expanduser()
        try:
            text = path.read_text(encoding="utf-8")[:20_000]
        except (OSError, UnicodeError):
            continue
        terms = _matched_visual_terms(text)
        if not terms:
            continue
        matched_terms.update(terms)
        artifact_matches.append({
            "kind": str(artifact.get("kind") or ""),
            "path": str(path),
            "terms": ", ".join(terms),
        })

    if user_facing:
        return {
            "required": True,
            "source": "explicit_user_facing",
            "matched_terms": sorted(matched_terms),
            "artifact_matches": artifact_matches,
        }
    if matched_terms:
        return {
            "required": True,
            "source": "auto_live_surface",
            "matched_terms": sorted(matched_terms),
            "artifact_matches": artifact_matches,
        }
    return {
        "required": False,
        "source": "not_user_facing",
        "matched_terms": [],
        "artifact_matches": [],
    }


def workflow_resume_prompt(state: State, *, run_id: str, task_id: str) -> dict[str, Any]:
    workflow = state.get_dual_agent_workflow(run_id=run_id, task_id=task_id)
    steps = state.list_dual_agent_workflow_steps(run_id=run_id, task_id=task_id)
    if workflow is None:
        return {"status": "not_found", "run_id": run_id, "task_id": task_id, "prompt": ""}

    last_step = steps[-1] if steps else None
    current_gate = str(workflow["current_gate"] or "")
    next_safe_action = _next_safe_action(
        workflow_status=str(workflow["status"]),
        current_gate=current_gate,
        last_step_status=str(last_step["status"]) if last_step is not None else "",
    )
    prompt = "\n".join([
        "Use codex_supervisor.",
        f"Continue run_id={run_id}",
        f"task_id={task_id}",
        f"current_gate={current_gate or 'unknown'}",
        f"last_status={workflow['status']}",
        f"next_safe_action={next_safe_action}",
    ])
    return {
        "status": "ok",
        "run_id": run_id,
        "task_id": task_id,
        "current_gate": current_gate,
        "last_status": workflow["status"],
        "next_safe_action": next_safe_action,
        "prompt": prompt,
    }


def claude_accepts(outcome: dict[str, Any] | None) -> bool:
    if not isinstance(outcome, dict):
        return False
    decisions = [str(value).lower() for value in outcome.get("decisions") or []]
    specialists = outcome.get("specialists") or []
    for specialist in specialists:
        if isinstance(specialist, dict):
            decisions.append(str(specialist.get("decision") or "").lower())
    return any("accept" in decision for decision in decisions)


def verify_workflow_claims(
    *,
    outcome_payload: dict[str, Any] | None,
    user_facing: bool,
    screenshots: list[dict[str, Any]],
    verified_claims: list[str] | None = None,
) -> ProbeResult:
    if not isinstance(outcome_payload, dict):
        return ProbeResult("P11", "red", "missing_outcome_for_claim_verification")
    outcome = Outcome(**outcome_payload)
    verified = {claim.lower() for claim in (verified_claims or [])}
    failures: list[str] = []
    claims = [claim.lower() for claim in outcome.claims]
    claims.extend(str(decision).lower() for decision in outcome.decisions)
    claims.append(str(outcome.summary).lower())

    joined = "\n".join(claims)
    if "tests passed" in joined or "test passed" in joined:
        if outcome.test_status != "passed" or not outcome.tests:
            failures.append("tests_passed_without_test_evidence")
    if "visual review passed" in joined or "visual validation passed" in joined or user_facing:
        if not _has_visual_evidence(screenshots):
            failures.append("visual_review_without_screenshot_evidence")
    if "implemented" in joined:
        if not outcome.changed_files:
            failures.append("implemented_without_changed_files")
    if "no files changed" in joined and outcome.changed_files:
        failures.append("no_files_changed_claim_with_changed_files")
    if "pushed" in joined and "pushed" not in verified:
        failures.append("pushed_without_remote_receipt")

    if failures:
        return ProbeResult("P11", "red", "workflow_claim_verification_failed", {"failures": failures})
    return ProbeResult("P11", "green", "workflow_claims_verified")


def workflow_milestone_text(*, task_id: str, milestone: str, gate: str | None = None) -> str:
    gate_text = f" gate={gate}" if gate else ""
    return f"[{task_id}] dual-agent workflow {milestone}{gate_text}"


def _seed_artifact_text(*, title: str, intent: str) -> str:
    return "\n".join([
        f"# {title}",
        "",
        "Generated by the supervisor-owned dual-agent workflow driver.",
        "",
        "## Intent",
        "",
        intent.strip() or "No intent supplied.",
        "",
    ])


def _matched_visual_terms(text: str) -> list[str]:
    normalized = text.lower().replace("_", "-")
    return [term for term in VISUAL_EVIDENCE_TERMS if term in normalized]


def _has_visual_evidence(screenshots: list[dict[str, Any]]) -> bool:
    for screenshot in screenshots:
        source = str(
            screenshot.get("source")
            or screenshot.get("captured_by")
            or ""
        ).lower()
        validation = screenshot.get("validation")
        if isinstance(validation, dict):
            status = str(validation.get("status") or "").lower()
        else:
            status = str(screenshot.get("validation_status") or "").lower()
        if source and status in {"passed", "pass", "accepted", "accept", "ok"}:
            return True
    return False


def _next_safe_action(
    *,
    workflow_status: str,
    current_gate: str,
    last_step_status: str,
) -> str:
    if workflow_status == "accepted":
        return "read_gate_transcript and inspect exported artifacts"
    if workflow_status in {"blocked", "failed"}:
        return "inspect blocker, provide corrective input, then rerun run_dual_agent_workflow"
    if last_step_status == "accepted":
        return "continue with the next workflow gate"
    if current_gate:
        return f"continue or repair {current_gate}"
    return "start workflow"
