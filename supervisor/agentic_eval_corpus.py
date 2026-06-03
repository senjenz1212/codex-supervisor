"""Golden corpus helpers for the agentic lead eval harness.

This module is intentionally report-only. It validates replay-backed labeled
sets and mines review-staged candidate cases, but it never executes workflow
arms or changes agentic policy.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


LABELED_SET_SCHEMA_VERSION = "agentic-lead-eval-labeled-set/v1"
CANDIDATE_SET_SCHEMA_VERSION = "agentic-lead-eval-candidate-set/v1"
EXECUTION_MODE = "fixture_replay"
ALLOWED_EVIDENCE_KINDS = {"probe_receipt", "artifact_path", "diff_hunk"}
ALLOWED_SOURCE_GATE_RESULTS = {"accept", "revise", "deny"}
DEFAULT_CURATED_CORPUS_PATH = Path("tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml")


def load_agentic_eval_labeled_set(
    path: str | Path,
    *,
    repo_root: str | Path | None = None,
    execution_mode: str = EXECUTION_MODE,
) -> dict[str, Any]:
    """Load and validate a curated agentic-lead eval labeled set."""
    if execution_mode != EXECUTION_MODE:
        raise ValueError("agentic eval corpus only supports execution_mode=fixture_replay")
    source_path = Path(path)
    raw = _load_structured_file(source_path)
    return validate_agentic_eval_labeled_set(
        raw,
        repo_root=repo_root,
        source_path=source_path,
    )


def validate_agentic_eval_labeled_set(
    data: dict[str, Any],
    *,
    repo_root: str | Path | None = None,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the labeled-set schema and return a normalized object."""
    if not isinstance(data, dict):
        raise ValueError("agentic eval labeled set must be an object")
    schema_version = str(data.get("schema_version") or "").strip()
    if schema_version != LABELED_SET_SCHEMA_VERSION:
        raise ValueError(
            f"agentic eval labeled set schema_version must be {LABELED_SET_SCHEMA_VERSION}"
        )
    execution_mode = str(data.get("execution_mode") or "").strip()
    if execution_mode != EXECUTION_MODE:
        raise ValueError("agentic eval labeled set execution_mode must be fixture_replay")

    root = Path(repo_root) if repo_root is not None else Path.cwd()
    tasks_raw = data.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError("agentic eval labeled set requires non-empty tasks")

    tasks = [
        _normalize_labeled_task(task=task, index=index, repo_root=root)
        for index, task in enumerate(tasks_raw)
    ]
    _validate_unique_task_ids(tasks)
    _validate_budget_shapes(tasks)

    observed_roster_sha = compute_agentic_eval_roster_sha256(tasks)
    expected_roster_sha = str(data.get("roster_sha256") or "").strip()
    if expected_roster_sha != observed_roster_sha:
        raise ValueError(
            "agentic eval labeled set roster_sha256 mismatch: "
            f"expected {expected_roster_sha or '<missing>'}, observed {observed_roster_sha}"
        )

    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    return {
        "schema_version": LABELED_SET_SCHEMA_VERSION,
        "execution_mode": EXECUTION_MODE,
        "source_path": str(source_path) if source_path is not None else None,
        "roster_sha256": observed_roster_sha,
        "provenance": {
            "curated": bool(provenance.get("curated", False)),
            "source": str(provenance.get("source") or "unknown"),
            "notes": list(provenance.get("notes") or []),
        },
        "tasks": tasks,
    }


def compute_agentic_eval_roster_sha256(tasks: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    """Hash the ordered case roster used by the curated corpus guard."""
    roster = [_roster_entry(task) for task in tasks]
    return _sha256_json(roster)


def mine_agentic_eval_cases(
    *,
    handoff_dir: str | Path,
    repo_root: str | Path | None = None,
    clean_accept_limit: int = 2,
    total_tokens: int = 12000,
    total_usd: float = 3.5,
) -> dict[str, Any]:
    """Mine workflow-result JSON files into review-staged candidate cases."""
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    handoff_path = Path(handoff_dir)
    result_files = sorted(
        {
            *handoff_path.glob("*-workflow-result.json"),
            *handoff_path.glob("*workflow-result.json"),
        },
        key=lambda item: item.name,
    )
    accepts: list[dict[str, Any]] = []
    non_accepts: list[dict[str, Any]] = []
    for result_file in result_files:
        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        source_gate_result = _source_gate_result(payload)
        candidate = _candidate_from_workflow_result(
            payload,
            result_file=result_file,
            repo_root=root,
            source_gate_result=source_gate_result,
            total_tokens=total_tokens,
            total_usd=total_usd,
        )
        if source_gate_result == "accept":
            accepts.append(candidate)
        else:
            non_accepts.append(candidate)

    selected = [*non_accepts, *accepts[: max(0, clean_accept_limit)]]
    selected = sorted(selected, key=lambda task: task["task_id"])
    candidate_set = {
        "schema_version": CANDIDATE_SET_SCHEMA_VERSION,
        "source_schema_version": LABELED_SET_SCHEMA_VERSION,
        "execution_mode": EXECUTION_MODE,
        "curation_required": True,
        "provenance": {
            "source": str(handoff_path),
            "result_file_count": len(result_files),
            "selected_non_accept_count": len(non_accepts),
            "selected_accept_count": min(len(accepts), max(0, clean_accept_limit)),
        },
        "roster_sha256": compute_agentic_eval_roster_sha256(selected),
        "tasks": selected,
    }
    return candidate_set


def write_agentic_eval_candidate_set(
    candidate_set: dict[str, Any],
    output_path: str | Path,
    *,
    curated_corpus_path: str | Path = DEFAULT_CURATED_CORPUS_PATH,
) -> Path:
    """Write mined candidates to a staging path, never the curated corpus."""
    output = Path(output_path)
    curated = Path(curated_corpus_path)
    if output.resolve() == curated.resolve():
        raise ValueError(f"refusing to write curated corpus: {curated}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(candidate_set, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return output


def _normalize_labeled_task(*, task: Any, index: int, repo_root: Path) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise ValueError(f"agentic eval corpus task at index {index} must be an object")
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        raise ValueError(f"agentic eval corpus task at index {index} is missing task_id")
    intent = str(task.get("intent") or "").strip()
    if not intent:
        raise ValueError(f"agentic eval corpus task {task_id} is missing intent")

    case_raw = task.get("case")
    if not isinstance(case_raw, dict):
        raise ValueError(f"agentic eval corpus task {task_id} requires case")
    expected_outcome = str(case_raw.get("expected_outcome") or "").strip()
    if expected_outcome not in ALLOWED_SOURCE_GATE_RESULTS:
        raise ValueError(f"agentic eval corpus task {task_id} has invalid expected_outcome")
    case = {
        "repo": str(case_raw.get("repo") or "").strip(),
        "commit": str(case_raw.get("commit") or "").strip(),
        "expected_outcome": expected_outcome,
        "case_types": [str(item) for item in case_raw.get("case_types") or []],
        "changed_files": [str(item) for item in case_raw.get("changed_files") or []],
    }
    if not case["repo"] or not case["commit"]:
        raise ValueError(f"agentic eval corpus task {task_id} case requires repo and commit")

    budget_raw = task.get("budget")
    if not isinstance(budget_raw, dict) or set(budget_raw) != {"total_tokens", "total_usd"}:
        raise ValueError(
            f"agentic eval corpus task {task_id} budget must contain only total_tokens and total_usd"
        )
    budget = {
        "total_tokens": _positive_number(budget_raw.get("total_tokens"), task_id=task_id, field="total_tokens"),
        "total_usd": _positive_number(budget_raw.get("total_usd"), task_id=task_id, field="total_usd"),
    }

    required_verdicts_raw = task.get("required_verdicts")
    if not isinstance(required_verdicts_raw, list) or not required_verdicts_raw:
        raise ValueError(f"agentic eval corpus task {task_id} requires required_verdicts")
    required_verdicts = [
        _normalize_required_verdict(task_id=task_id, item=item, index=item_index, repo_root=repo_root)
        for item_index, item in enumerate(required_verdicts_raw)
    ]

    transcript_refs_raw = task.get("transcript_refs")
    if not isinstance(transcript_refs_raw, list) or not transcript_refs_raw:
        raise ValueError(f"agentic eval corpus task {task_id} requires transcript_refs")
    transcript_refs = [
        _normalize_transcript_ref(task_id=task_id, item=item, index=item_index, repo_root=repo_root)
        for item_index, item in enumerate(transcript_refs_raw)
    ]

    provenance_raw = task.get("provenance")
    if not isinstance(provenance_raw, dict):
        raise ValueError(f"agentic eval corpus task {task_id} requires provenance")
    source_gate_result = str(provenance_raw.get("source_gate_result") or "").strip()
    if source_gate_result not in ALLOWED_SOURCE_GATE_RESULTS:
        raise ValueError(f"agentic eval corpus task {task_id} provenance source_gate_result is invalid")
    mined_from = str(provenance_raw.get("mined_from") or "").strip()
    if not mined_from:
        raise ValueError(f"agentic eval corpus task {task_id} provenance mined_from is required")
    provenance = {
        "mined_from": mined_from,
        "source_gate_result": source_gate_result,
        "curated_reason": str(provenance_raw.get("curated_reason") or ""),
    }

    return {
        "schema_version": "agentic-lead-eval-labeled-task/v1",
        "task_id": task_id,
        "intent": intent,
        "case": case,
        "budget": budget,
        "required_verdicts": required_verdicts,
        "transcript_refs": transcript_refs,
        "provenance": provenance,
    }


def _normalize_required_verdict(
    *,
    task_id: str,
    item: Any,
    index: int,
    repo_root: Path,
) -> dict[str, str]:
    if not isinstance(item, dict):
        raise ValueError(f"agentic eval corpus task {task_id} verdict {index} must be an object")
    verdict_id = str(item.get("id") or "").strip()
    evidence_kind = str(item.get("evidence_kind") or "").strip()
    evidence_ref = str(item.get("evidence_ref") or "").strip()
    if not verdict_id:
        raise ValueError(f"agentic eval corpus task {task_id} verdict {index} is missing id")
    if evidence_kind not in ALLOWED_EVIDENCE_KINDS:
        raise ValueError(f"agentic eval corpus task {task_id} verdict {verdict_id} has invalid evidence_kind")
    if not evidence_ref:
        raise ValueError(f"agentic eval corpus task {task_id} verdict {verdict_id} is missing evidence_ref")
    if not _ref_exists(evidence_ref, repo_root=repo_root, evidence_kind=evidence_kind):
        raise ValueError(f"agentic eval corpus task {task_id} has missing evidence_ref: {evidence_ref}")
    return {
        "id": verdict_id,
        "description": str(item.get("description") or verdict_id),
        "evidence_kind": evidence_kind,
        "evidence_ref": evidence_ref,
    }


def _normalize_transcript_ref(
    *,
    task_id: str,
    item: Any,
    index: int,
    repo_root: Path,
) -> dict[str, str]:
    if not isinstance(item, dict):
        raise ValueError(f"agentic eval corpus task {task_id} transcript ref {index} must be an object")
    kind = str(item.get("kind") or "").strip()
    ref = str(item.get("ref") or "").strip()
    if kind != "cassette":
        raise ValueError(f"agentic eval corpus task {task_id} transcript ref {index} must be kind=cassette")
    if not _ref_exists(ref, repo_root=repo_root, evidence_kind="artifact_path"):
        raise ValueError(f"agentic eval corpus task {task_id} has missing transcript ref: {ref}")
    return {"kind": kind, "ref": ref}


def _candidate_from_workflow_result(
    payload: dict[str, Any],
    *,
    result_file: Path,
    repo_root: Path,
    source_gate_result: str,
    total_tokens: int,
    total_usd: float,
) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or result_file.stem.replace("-workflow-result", "")).strip()
    current_gate = str(payload.get("current_gate") or _final_gate(payload) or "outcome_review")
    evidence_ref = _relative_ref(result_file, repo_root=repo_root)
    return {
        "task_id": task_id,
        "intent": f"Mined workflow outcome for {task_id}.",
        "case": {
            "repo": repo_root.name or "repo",
            "commit": str(payload.get("commit") or payload.get("head_sha") or "unknown"),
            "expected_outcome": source_gate_result,
            "case_types": _case_types(payload, source_gate_result=source_gate_result),
            "changed_files": _changed_files(payload),
        },
        "budget": {
            "total_tokens": total_tokens,
            "total_usd": total_usd,
        },
        "required_verdicts": [
            {
                "id": f"{current_gate}_decision_recorded",
                "description": "Workflow final gate decision is captured for eval scoring.",
                "evidence_kind": "probe_receipt",
                "evidence_ref": evidence_ref,
            },
            {
                "id": "artifact_export_recorded",
                "description": "Workflow artifact export or result artifact is available for replay.",
                "evidence_kind": "artifact_path",
                "evidence_ref": evidence_ref,
            },
        ],
        "transcript_refs": [{"kind": "cassette", "ref": evidence_ref}],
        "provenance": {
            "mined_from": evidence_ref,
            "source_gate_result": source_gate_result,
            "curated_reason": "staged_candidate_requires_human_review",
        },
    }


def _source_gate_result(payload: dict[str, Any]) -> str:
    final = payload.get("final_gate_result") if isinstance(payload.get("final_gate_result"), dict) else {}
    values = [
        final.get("codex_decision"),
        final.get("claude_decision"),
        final.get("cursor_decision"),
        final.get("status"),
        payload.get("status"),
    ]
    normalized = {str(value or "").strip().lower() for value in values}
    if normalized & {"deny", "denied", "rejected"}:
        return "deny"
    if normalized & {"revise", "blocked", "failed"}:
        return "revise"
    return "accept"


def _final_gate(payload: dict[str, Any]) -> str:
    final = payload.get("final_gate_result") if isinstance(payload.get("final_gate_result"), dict) else {}
    return str(final.get("gate") or "").strip()


def _case_types(payload: dict[str, Any], *, source_gate_result: str) -> list[str]:
    types = ["clean_accept" if source_gate_result == "accept" else f"{source_gate_result}_required"]
    final = payload.get("final_gate_result") if isinstance(payload.get("final_gate_result"), dict) else {}
    changed_files = _changed_files(payload)
    if _final_gate(payload) in {"prd_review", "issues_review", "tdd_review", "implementation_plan"}:
        types.append("artifact_only_gate")
    if len(changed_files) > 1:
        types.append("multi_file_change")
    if final.get("artifact_rigor"):
        types.append("artifact_rigor")
    return sorted(set(types))


def _changed_files(payload: dict[str, Any]) -> list[str]:
    final = payload.get("final_gate_result") if isinstance(payload.get("final_gate_result"), dict) else {}
    outcome = final.get("outcome") if isinstance(final.get("outcome"), dict) else {}
    changed_files = outcome.get("changed_files") if isinstance(outcome.get("changed_files"), list) else []
    return [str(item) for item in changed_files]


def _validate_unique_task_ids(tasks: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for task in tasks:
        task_id = task["task_id"]
        if task_id in seen:
            raise ValueError(f"duplicate agentic eval corpus task_id: {task_id}")
        seen.add(task_id)


def _validate_budget_shapes(tasks: list[dict[str, Any]]) -> None:
    shapes = {tuple(sorted(task["budget"])) for task in tasks}
    if shapes != {("total_tokens", "total_usd")}:
        raise ValueError("agentic eval corpus tasks must use identical total budget shape")


def _positive_number(value: Any, *, task_id: str, field: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"agentic eval corpus task {task_id} budget {field} must be numeric")
    if isinstance(value, int | float) and value > 0:
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"agentic eval corpus task {task_id} budget {field} must be numeric") from exc
    if parsed <= 0:
        raise ValueError(f"agentic eval corpus task {task_id} budget {field} must be positive")
    return int(parsed) if parsed.is_integer() else parsed


def _roster_entry(task: dict[str, Any]) -> dict[str, Any]:
    case = task.get("case") if isinstance(task.get("case"), dict) else {}
    provenance = task.get("provenance") if isinstance(task.get("provenance"), dict) else {}
    return {
        "task_id": str(task.get("task_id") or ""),
        "expected_outcome": str(case.get("expected_outcome") or ""),
        "source_gate_result": str(provenance.get("source_gate_result") or ""),
        "budget": dict(task.get("budget") or {}),
        "required_verdicts": [
            {
                "id": str(item.get("id") or ""),
                "evidence_kind": str(item.get("evidence_kind") or ""),
                "evidence_ref": str(item.get("evidence_ref") or ""),
            }
            for item in list(task.get("required_verdicts") or [])
            if isinstance(item, dict)
        ],
        "transcript_refs": [
            {
                "kind": str(item.get("kind") or ""),
                "ref": str(item.get("ref") or ""),
            }
            for item in list(task.get("transcript_refs") or [])
            if isinstance(item, dict)
        ],
    }


def _ref_exists(ref: str, *, repo_root: Path, evidence_kind: str) -> bool:
    if evidence_kind == "diff_hunk":
        return _diff_hunk_ref_exists(ref, repo_root=repo_root)
    path_ref = ref
    path = Path(path_ref)
    if path.is_absolute():
        return path.exists()
    return (repo_root / path).exists()


def _diff_hunk_ref_exists(ref: str, *, repo_root: Path) -> bool:
    path_ref, separator, hunk_label = ref.partition(":")
    path = Path(path_ref)
    if not path.is_absolute():
        path = repo_root / path
    if not path.exists():
        return False
    if not separator:
        return True
    label = hunk_label.strip()
    if not label:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(_diff_hunk_line_matches_label(line, label) for line in text.splitlines())


def _diff_hunk_line_matches_label(line: str, label: str) -> bool:
    marker = line.strip()
    if not marker.startswith("@@"):
        return False
    payload = marker[2:].strip()
    if payload.endswith("@@"):
        payload = payload[:-2].strip()
    if " @@" in payload:
        payload = payload.rsplit(" @@", 1)[-1].strip()
    return payload == label or payload.endswith(f" {label}")


def _relative_ref(path: Path, *, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def _load_structured_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("agentic eval corpus file must contain an object")
    return data


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
