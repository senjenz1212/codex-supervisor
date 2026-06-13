"""Deterministic cross-run lessons for supervised workflow failures."""
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable

from .failure_taxonomy import classify_failure, detect_sequence_failures, failure_taxonomy_for_payload


LESSON_SCHEMA_VERSION = "supervisor-lesson/v1"
LESSON_INJECTION_SCHEMA_VERSION = "supervisor-lesson-injection/v1"
LESSON_BLOCK_HEADER = "Known failure modes to verify before claiming"
DEFAULT_LESSON_LIMIT = 5


@dataclass(frozen=True)
class SupervisorLesson:
    lesson_id: str
    task_class: str
    gate: str
    taxonomy_code: str
    root_cause: str
    remediation: str
    source_run_id: str
    created_at: int

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SupervisorLesson":
        return cls(
            lesson_id=_text(value.get("lesson_id")),
            task_class=_normalise_task_class(value.get("task_class")),
            gate=_normalise_gate(value.get("gate")),
            taxonomy_code=_text(value.get("taxonomy_code")),
            root_cause=_text(value.get("root_cause")),
            remediation=_text(value.get("remediation")),
            source_run_id=_text(value.get("source_run_id")),
            created_at=_int(value.get("created_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LESSON_SCHEMA_VERSION,
            "lesson_id": self.lesson_id,
            "task_class": self.task_class,
            "gate": self.gate,
            "taxonomy_code": self.taxonomy_code,
            "root_cause": self.root_cause,
            "remediation": self.remediation,
            "source_run_id": self.source_run_id,
            "created_at": self.created_at,
        }


def canonical_lesson_id(
    *,
    task_class: str,
    gate: str,
    taxonomy_code: str,
    root_cause: str,
    remediation: str,
    source_run_id: str,
) -> str:
    return canonical_lesson_key(
        task_class=task_class,
        gate=gate,
        taxonomy_code=taxonomy_code,
        root_cause=root_cause,
        remediation=remediation,
    )


def canonical_lesson_key(
    *,
    task_class: str,
    gate: str,
    taxonomy_code: str,
    root_cause: str,
    remediation: str,
) -> str:
    body = _canonical_json({
        "task_class": _normalise_task_class(task_class),
        "gate": _normalise_gate(gate),
        "taxonomy_code": _text(taxonomy_code),
        "root_cause": _normalise_lesson_text(root_cause),
        "remediation": _normalise_lesson_text(remediation),
    })
    return "lesson-" + sha256(body.encode("utf-8")).hexdigest()


def build_lesson_injection(
    lessons: Iterable[dict[str, Any] | SupervisorLesson],
    *,
    limit: int = DEFAULT_LESSON_LIMIT,
) -> dict[str, Any]:
    normalized = [_lesson_from_any(item) for item in lessons]
    ordered = sorted(
        normalized,
        key=lambda item: (-int(item.created_at), item.lesson_id),
    )[: max(0, int(limit))]
    if not ordered:
        return {
            "schema_version": LESSON_INJECTION_SCHEMA_VERSION,
            "lesson_ids": [],
            "lesson_count": 0,
            "block": "",
            "block_sha256": sha256(b"").hexdigest(),
        }

    lines = [
        LESSON_BLOCK_HEADER,
        "These are advisory lessons from previous supervisor runs. They do not satisfy any gate by themselves.",
        (
            "Use them as a checklist only: do not block, revise, deny, or accept solely because "
            "a lesson exists. A step-repetition lesson applies only when current evidence proves "
            "the same handoff, artifacts, and source state are being repeated."
        ),
    ]
    for index, lesson in enumerate(ordered, start=1):
        lines.append(
            f"{index}. [{lesson.taxonomy_code}] {lesson.root_cause} "
            f"(source_run_id={lesson.source_run_id}): {lesson.remediation}"
        )
    block = "\n".join(lines)
    return {
        "schema_version": LESSON_INJECTION_SCHEMA_VERSION,
        "lesson_ids": [lesson.lesson_id for lesson in ordered],
        "lesson_count": len(ordered),
        "block": block,
        "block_sha256": sha256(block.encode("utf-8")).hexdigest(),
    }


def append_lesson_block(instruction: str, injection: dict[str, Any]) -> str:
    block = _text(injection.get("block"))
    if not block:
        return instruction
    if LESSON_BLOCK_HEADER in instruction:
        return instruction
    return instruction.rstrip() + "\n\n" + block


def derive_lessons_from_events(
    events: Iterable[dict[str, Any]],
    *,
    task_class: str,
    source_run_id: str,
) -> list[dict[str, Any]]:
    normalized_events = [_normalise_event(event) for event in events]
    lessons: dict[str, dict[str, Any]] = {}

    for event in normalized_events:
        if not _eligible_event(event):
            continue
        kind = _text(event.get("kind"))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        gate = _normalise_gate(payload.get("gate") or event.get("gate") or "unknown")

        taxonomy = _taxonomy_for_event(kind=kind, payload=payload)
        if taxonomy is not None:
            _add_lesson(
                lessons,
                task_class=task_class,
                gate=gate,
                source_run_id=source_run_id,
                taxonomy=taxonomy,
            )

        reviewer_taxonomy = _reviewer_disagreement_taxonomy(kind=kind, payload=payload)
        if reviewer_taxonomy is not None:
            _add_lesson(
                lessons,
                task_class=task_class,
                gate=gate,
                source_run_id=source_run_id,
                taxonomy=reviewer_taxonomy,
            )

        drift_taxonomy = _drift_or_probe_taxonomy(event=event, payload=payload)
        if drift_taxonomy is not None:
            _add_lesson(
                lessons,
                task_class=task_class,
                gate=gate,
                source_run_id=source_run_id,
                taxonomy=drift_taxonomy,
            )

    for sequence_failure in detect_sequence_failures(normalized_events):
        gate = _gate_from_sequence_failure(sequence_failure)
        _add_lesson(
            lessons,
            task_class=task_class,
            gate=gate,
            source_run_id=source_run_id,
            taxonomy=sequence_failure,
        )

    return [lessons[key] for key in sorted(lessons)]


OPS_LESSON_TASK_CLASS = "supervisor_ops"
OPS_LESSON_GATE = "pre_flight"
OPS_LESSON_TAXONOMY_CODE = "OPS-1.0"
OPS_LESSON_ROOT_CAUSE = "pre_flight_hygiene_drift"
OPS_LESSON_REMEDIATION = (
    "Run codex-supervisor-axi doctor before submitting work; "
    "resolve any degraded daemon, stale lease, or overdue audit hint before proceeding."
)
OPS_LESSON_SOURCE_RUN_ID = "supervisor-ops-backfill"


def backfill_operational_lessons(state: Any) -> list[dict[str, Any]]:
    """Idempotently record the canonical supervisor_ops/pre_flight lesson.

    Returns the recorded lesson rows. Re-running yields the same lesson_id and
    leaves created=False for repeat calls. Advisory-only; lesson injection
    cannot advance or block a gate.
    """

    row, created = state.record_supervisor_lesson(
        task_class=OPS_LESSON_TASK_CLASS,
        gate=OPS_LESSON_GATE,
        taxonomy_code=OPS_LESSON_TAXONOMY_CODE,
        root_cause=OPS_LESSON_ROOT_CAUSE,
        remediation=OPS_LESSON_REMEDIATION,
        source_run_id=OPS_LESSON_SOURCE_RUN_ID,
    )
    lesson = dict(row)
    lesson["created"] = bool(created)
    return [lesson]


def record_lessons_for_run(
    state: Any,
    *,
    run_id: str,
    task_id: str,
    task_class: str,
    event_limit: int = 10000,
) -> list[dict[str, Any]]:
    events = state.read_events_since(run_id, after_event_id=0, limit=event_limit)
    lesson_inputs = derive_lessons_from_events(
        events,
        task_class=task_class,
        source_run_id=run_id,
    )
    recorded: list[dict[str, Any]] = []
    for lesson in lesson_inputs:
        row, created = state.record_supervisor_lesson(**lesson)
        lesson_payload = dict(row)
        lesson_payload["created"] = bool(created)
        recorded.append(lesson_payload)
        if created:
            state.write_event(
                run_id=run_id,
                source="supervisor",
                kind="supervisor_lesson_recorded",
                payload={
                    "schema_version": LESSON_SCHEMA_VERSION,
                    "task_id": task_id,
                    "lesson": lesson_payload,
                    "advisory_only": True,
                    "gate_authority": "unchanged",
                },
            )
    return recorded


def _add_lesson(
    lessons: dict[str, dict[str, Any]],
    *,
    task_class: str,
    gate: str,
    source_run_id: str,
    taxonomy: dict[str, Any],
) -> None:
    taxonomy_code = _taxonomy_code(taxonomy)
    root_cause = _root_cause(taxonomy)
    remediation = remediation_for_taxonomy(taxonomy)
    lesson_id = canonical_lesson_id(
        task_class=task_class,
        gate=gate,
        taxonomy_code=taxonomy_code,
        root_cause=root_cause,
        remediation=remediation,
        source_run_id=source_run_id,
    )
    lessons[lesson_id] = {
        "task_class": _normalise_task_class(task_class),
        "gate": _normalise_gate(gate),
        "taxonomy_code": taxonomy_code,
        "root_cause": root_cause,
        "remediation": remediation,
        "source_run_id": _text(source_run_id),
    }


def remediation_for_taxonomy(taxonomy: dict[str, Any]) -> str:
    code = _taxonomy_code(taxonomy)
    text = " ".join(
        _text(taxonomy.get(key)).lower()
        for key in ("code", "mast_code", "subcategory", "mast_mode", "probe_id")
    )
    if code == "FM-3.2" or "receipt" in text or "claim" in text:
        return "Verify the claim with supervisor-generated receipts before reporting acceptance."
    if code == "FM-3.3" or "false_green" in text or "incorrect_verification" in text:
        return "Re-run validation and reconcile reviewer objections before declaring the gate green."
    if code == "FM-2.4" or "reviewer_disagreement" in text:
        return "Address independent reviewer objections with concrete evidence references."
    if code == "FM-1.3" or "repeated_gate" in text or "step_repetition" in text:
        return "Change the plan or evidence before retrying; do not repeat the same handoff."
    if code == "FM-1.1" or "artifact" in text or "planning" in text:
        return "Repair the planning artifact or traceability gap before invoking the lead again."
    if "drift" in text or "probe" in text:
        return "Resolve the failing deterministic probe and cite its new green receipt."
    return "Verify this known failure mode explicitly before claiming the gate is complete."


def _taxonomy_for_event(*, kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    envelope = payload.get("trace_envelope") if isinstance(payload.get("trace_envelope"), dict) else {}
    taxonomy = envelope.get("failure_taxonomy") if isinstance(envelope, dict) else None
    if isinstance(taxonomy, dict) and taxonomy:
        return taxonomy
    return failure_taxonomy_for_payload(kind=kind, payload=payload)


def _reviewer_disagreement_taxonomy(*, kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if kind not in {"tri_agent_cursor_review", "independent_reviewer_review"}:
        return None
    panel = payload.get("independent_reviewer_panel_decision")
    if isinstance(panel, dict) and _text(panel.get("decision")) not in {"", "accept"}:
        return classify_failure(
            reason="reviewer_disagreement",
            probe_id="CURSOR",
            source=kind,
            details={"panel_decision": panel},
        )
    results = payload.get("independent_reviewer_results")
    if isinstance(results, list) and any(
        isinstance(item, dict) and item.get("accepted") is False for item in results
    ):
        return classify_failure(
            reason="reviewer_disagreement",
            probe_id="CURSOR",
            source=kind,
        )
    cursor_review = payload.get("cursor_review")
    if isinstance(cursor_review, dict) and cursor_review.get("accepted") is False:
        return classify_failure(
            reason="reviewer_disagreement",
            probe_id="CURSOR",
            source=kind,
        )
    return None


def _drift_or_probe_taxonomy(*, event: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    kind = _text(event.get("kind")).lower()
    source = _text(event.get("source")).lower()
    status = _text(payload.get("status") or payload.get("verdict")).lower()
    has_probe_cohort = isinstance(payload.get("probe_cohort"), dict) or isinstance(
        payload.get("probe_cohorts"),
        (list, tuple),
    )
    if not (source == "drift" or "drift" in kind or has_probe_cohort):
        return None
    if status in {"", "ok", "green", "accepted", "passed", "pass"}:
        return None
    return classify_failure(
        reason="drift_probe_cohort_failure",
        source=_text(event.get("kind")),
        details={"status": status or "unknown"},
    )


def _normalise_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict) and isinstance(event.get("payload_json"), str):
        try:
            loaded = json.loads(str(event["payload_json"]))
        except json.JSONDecodeError:
            loaded = {}
        payload = loaded if isinstance(loaded, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        **event,
        "kind": _text(event.get("kind") or payload.get("kind")),
        "source": _text(event.get("source") or payload.get("source")),
        "gate": _normalise_gate(event.get("gate") or payload.get("gate")),
        "payload": payload,
    }


def _eligible_event(event: dict[str, Any]) -> bool:
    kind = _text(event.get("kind"))
    if kind.startswith("supervisor_lesson_"):
        return False
    if kind in {
        "dual_agent_gate_result",
        "dual_agent_planning_validation",
        "dual_agent_skill_receipt_validation",
        "dual_agent_dynamic_workflow_receipt_validation",
        "dual_agent_runtime_evidence",
        "tri_agent_cursor_review",
        "independent_reviewer_review",
    }:
        return True
    source = _text(event.get("source")).lower()
    return source == "drift" or "drift" in kind.lower()


def _gate_from_sequence_failure(failure: dict[str, Any]) -> str:
    details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
    signature = details.get("signature")
    if isinstance(signature, (list, tuple)) and signature:
        return _normalise_gate(signature[0])
    return _normalise_gate(failure.get("gate") or "unknown")


def _taxonomy_code(taxonomy: dict[str, Any]) -> str:
    return (
        _text(taxonomy.get("mast_code"))
        or _text(taxonomy.get("code"))
        or _text(taxonomy.get("subcategory"))
        or "unknown_failure"
    )


def _root_cause(taxonomy: dict[str, Any]) -> str:
    return (
        _text(taxonomy.get("mast_mode"))
        or _text(taxonomy.get("subcategory"))
        or _text(taxonomy.get("code"))
        or "unknown failure"
    )


def _lesson_from_any(value: dict[str, Any] | SupervisorLesson) -> SupervisorLesson:
    if isinstance(value, SupervisorLesson):
        return value
    return SupervisorLesson.from_mapping(value)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalise_task_class(value: Any) -> str:
    text = _text(value).replace("-", "_")
    return text or "general"


def _normalise_gate(value: Any) -> str:
    text = _text(value).replace("-", "_")
    return text or "unknown"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalise_lesson_text(value: Any) -> str:
    return " ".join(_text(value).lower().split())


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
