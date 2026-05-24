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

REQUIRED_PRD_TDD_SKILL_STAGES: tuple[str, ...] = (
    "to_prd",
    "prd_grill",
    "to_issues",
    "tdd",
    "tdd_grill",
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
    step_payloads = [
        {
            "gate": str(step["gate"]),
            "status": str(step["status"]),
            "attempt_count": int(step["attempt_count"]),
            "latest_event_id": step["latest_event_id"],
        }
        for step in steps
    ]
    latest_event_id = state.latest_event_id(run_id)
    artifact_output_dir = default_dual_agent_artifact_dir(str(workflow["cwd"]), task_id)
    blocker = None
    if str(workflow["status"]) in {"blocked", "failed"}:
        blocker = {
            "gate": current_gate or (str(last_step["gate"]) if last_step is not None else ""),
            "status": str(workflow["status"]),
            "latest_event_id": (
                int(last_step["latest_event_id"])
                if last_step is not None and last_step["latest_event_id"] is not None
                else latest_event_id
            ),
        }
    next_safe_action = _next_safe_action(
        workflow_status=str(workflow["status"]),
        current_gate=current_gate,
        last_step_status=str(last_step["status"]) if last_step is not None else "",
    )
    transcript_command = f"read_gate_transcript(run_id={run_id}, task_id={task_id})"
    prompt = "\n".join([
        "Use codex_supervisor.",
        f"Continue run_id={run_id}",
        f"task_id={task_id}",
        f"current_gate={current_gate or 'unknown'}",
        f"last_status={workflow['status']}",
        f"latest_event_id={latest_event_id}",
        f"next_safe_action={next_safe_action}",
        f"artifact_output_dir={artifact_output_dir}",
        f"first inspect: {transcript_command}",
    ])
    return {
        "status": "ok",
        "run_id": run_id,
        "task_id": task_id,
        "current_gate": current_gate,
        "last_status": workflow["status"],
        "steps": step_payloads,
        "latest_event_id": latest_event_id,
        "blocker": blocker,
        "artifact_output_dir": str(artifact_output_dir),
        "transcript_command": transcript_command,
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
    tool_receipts: list[dict[str, Any]] | None = None,
) -> ProbeResult:
    if not isinstance(outcome_payload, dict):
        return ProbeResult("P11", "red", "missing_outcome_for_claim_verification")
    outcome = Outcome(**outcome_payload)
    receipts = _normalise_tool_receipts(tool_receipts or [], screenshots=screenshots)
    legacy_verified = sorted({claim.lower() for claim in (verified_claims or [])})
    failures: list[str] = []
    claims = [claim.lower() for claim in outcome.claims]
    claims.extend(str(decision).lower() for decision in outcome.decisions)
    claims.append(str(outcome.summary).lower())

    joined = "\n".join(claims)
    if "tests passed" in joined or "test passed" in joined:
        if outcome.test_status != "passed" or not outcome.tests:
            failures.append("tests_passed_without_test_evidence")
        if not _has_receipt(
            receipts,
            kinds={"test", "pytest", "unit_test"},
            statuses=_PASSING_RECEIPT_STATUSES,
            claim="tests passed",
            require_claim=True,
        ):
            failures.append("tests_passed_without_test_receipt")
    if "visual review passed" in joined or "visual validation passed" in joined or user_facing:
        if not _has_visual_evidence(screenshots):
            failures.append("visual_review_without_screenshot_evidence")
        if not _has_receipt(
            receipts,
            kinds={"visual", "screenshot", "computer_use", "browser"},
            statuses=_PASSING_RECEIPT_STATUSES,
            claim="visual review passed",
        ):
            failures.append("visual_review_without_visual_receipt")
    if "implemented" in joined:
        if not outcome.changed_files:
            failures.append("implemented_without_changed_files")
        if not _has_receipt(
            receipts,
            kinds={"git_diff", "changed_files", "implementation"},
            statuses=_PRESENT_RECEIPT_STATUSES,
            claim="implemented",
            require_claim=True,
            changed_files=outcome.changed_files or [],
        ):
            failures.append("implemented_without_diff_receipt")
    if "no files changed" in joined and outcome.changed_files:
        failures.append("no_files_changed_claim_with_changed_files")
    if "no files changed" in joined and not _has_receipt(
        receipts,
        kinds={"git_status"},
        statuses={"clean", *_PASSING_RECEIPT_STATUSES},
        claim="no files changed",
        require_claim=True,
    ):
        failures.append("no_files_changed_without_git_status_receipt")
    if "pushed" in joined and not _has_receipt(
        receipts,
        kinds={"git_remote", "push", "git_push"},
        statuses={"pushed", *_PASSING_RECEIPT_STATUSES},
        claim="pushed",
        require_claim=True,
    ):
        failures.append("pushed_without_remote_receipt")

    details = {
        "receipts": receipts,
        "legacy_verified_claims": legacy_verified,
    }
    if failures:
        return ProbeResult("P11", "red", "workflow_claim_verification_failed", {**details, "failures": failures})
    return ProbeResult("P11", "green", "workflow_claims_verified", details)


def verify_prd_tdd_skill_receipts(
    tool_receipts: list[dict[str, Any]] | None,
    *,
    required_stages: tuple[str, ...] = REQUIRED_PRD_TDD_SKILL_STAGES,
) -> ProbeResult:
    receipts = _normalise_tool_receipts(tool_receipts or [], screenshots=[])
    observed: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        kind = _normalise_receipt_value(receipt.get("kind") or receipt.get("type"))
        if kind not in {"skill_run", "prd_tdd_skill", "skill"}:
            continue
        status = _normalise_receipt_value(receipt.get("status") or receipt.get("result"))
        if status not in _PASSING_RECEIPT_STATUSES:
            continue
        stage = _skill_receipt_stage(receipt)
        if stage:
            observed.setdefault(stage, receipt)

    missing = [stage for stage in required_stages if stage not in observed]
    details = {
        "required_stages": list(required_stages),
        "observed_stages": sorted(observed),
        "receipts": [observed[stage] for stage in sorted(observed)],
    }
    if missing:
        return ProbeResult(
            "P12",
            "red",
            "missing_prd_tdd_skill_receipts",
            {**details, "missing_stages": missing},
        )
    return ProbeResult("P12", "green", "prd_tdd_skill_receipts_verified", details)


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


_PASSING_RECEIPT_STATUSES = {"passed", "pass", "accepted", "accept", "ok", "success", "succeeded"}
_PRESENT_RECEIPT_STATUSES = {*_PASSING_RECEIPT_STATUSES, "present", "changed", "exists"}


def _normalise_tool_receipts(
    tool_receipts: list[dict[str, Any]],
    *,
    screenshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for receipt in tool_receipts:
        if not isinstance(receipt, dict):
            continue
        item = dict(receipt)
        item["kind"] = _normalise_receipt_value(item.get("kind") or item.get("type"))
        item["status"] = _normalise_receipt_value(item.get("status") or item.get("result"))
        receipts.append(item)

    for index, screenshot in enumerate(screenshots, start=1):
        if not isinstance(screenshot, dict):
            continue
        validation = screenshot.get("validation")
        validation_payload = validation if isinstance(validation, dict) else {}
        status = _normalise_receipt_value(
            screenshot.get("validation_status")
            or validation_payload.get("status")
            or validation_payload.get("result")
        )
        source = _normalise_receipt_value(
            screenshot.get("source")
            or screenshot.get("captured_by")
            or screenshot.get("tool")
        )
        if source and status:
            receipts.append({
                "receipt_id": screenshot.get("receipt_id") or f"screenshot-{index}",
                "kind": "visual",
                "status": status,
                "source": source,
                "path": screenshot.get("path"),
                "label": screenshot.get("label"),
            })
    return receipts


def _has_receipt(
    receipts: list[dict[str, Any]],
    *,
    kinds: set[str],
    statuses: set[str],
    claim: str | None = None,
    require_claim: bool = False,
    changed_files: list[str] | None = None,
) -> bool:
    for receipt in receipts:
        kind = _normalise_receipt_value(receipt.get("kind") or receipt.get("type"))
        status = _normalise_receipt_value(receipt.get("status") or receipt.get("result"))
        if kind not in kinds or status not in statuses:
            continue
        if claim is not None and not _receipt_matches_claim(
            receipt,
            claim,
            require_claim=require_claim,
        ):
            continue
        if changed_files and not _receipt_covers_changed_files(receipt, changed_files):
            continue
        return True
    return False


def _normalise_receipt_value(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _skill_receipt_stage(receipt: dict[str, Any]) -> str:
    stage = _normalise_receipt_value(receipt.get("stage") or receipt.get("phase"))
    skill = _normalise_receipt_value(receipt.get("skill") or receipt.get("skill_name"))
    if stage:
        return stage
    if skill == "to_prd":
        return "to_prd"
    if skill == "to_issues":
        return "to_issues"
    if skill == "tdd":
        return "tdd"
    return ""


def _receipt_matches_claim(
    receipt: dict[str, Any],
    claim: str,
    *,
    require_claim: bool,
) -> bool:
    claim_values = receipt.get("claims") or receipt.get("claim")
    if claim_values is None:
        return not require_claim
    if isinstance(claim_values, str):
        claim_items = [claim_values]
    elif isinstance(claim_values, (list, tuple, set)):
        claim_items = [str(item) for item in claim_values]
    else:
        claim_items = [str(claim_values)]
    expected = _normalise_claim_text(claim)
    return any(_claim_text_matches(_normalise_claim_text(item), expected) for item in claim_items)


def _claim_text_matches(value: str, expected: str) -> bool:
    if not value or not expected:
        return False
    return value == expected or expected in value or value in expected


def _receipt_covers_changed_files(
    receipt: dict[str, Any],
    changed_files: list[str],
) -> bool:
    receipt_files = receipt.get("changed_files") or receipt.get("files") or receipt.get("paths")
    if receipt_files is None:
        return True
    if isinstance(receipt_files, str):
        receipt_file_set = {receipt_files}
    elif isinstance(receipt_files, (list, tuple, set)):
        receipt_file_set = {str(item) for item in receipt_files}
    else:
        receipt_file_set = {str(receipt_files)}
    expected = {str(path) for path in changed_files if str(path).strip()}
    return bool(expected & receipt_file_set)


def _normalise_claim_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


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
