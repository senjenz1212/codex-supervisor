"""Report-only durable-execution engine spike helpers.

The production supervisor stays on the hand-rolled Layer 0/0.5/1 stack. This
module models one optional Temporal submit lifecycle so the ADR can compare
behavior without adding a live Temporal dependency or changing defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol


TEMPORAL_USE_EXISTING_POLICY = "USE_EXISTING"
TEMPORAL_WORKFLOW_NAME = "codex_supervisor.dual_agent_workflow_job"


class TemporalSubmitClient(Protocol):
    def start_workflow(
        self,
        workflow: str,
        payload: dict[str, Any],
        *,
        id: str,
        task_queue: str,
        id_conflict_policy: str,
    ) -> Any:
        """Start or attach to a workflow by idempotency-key workflow id."""


@dataclass(frozen=True)
class TemporalSpikeConfig:
    enabled: bool = False
    task_queue: str = "codex-supervisor-spike"
    workflow_name: str = TEMPORAL_WORKFLOW_NAME


@dataclass(frozen=True)
class DurableSubmitTask:
    task_id: str
    run_id: str
    idempotency_key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SubmitHandle:
    system: Literal["layer0", "temporal"]
    task_id: str
    handle_id: str
    created: bool | None
    reattached: bool
    recovery_point: str | None = None
    workflow_id_source: str | None = None


@dataclass(frozen=True)
class SubmitComparison:
    task_id: str
    system: Literal["layer0", "temporal"]
    first_handle_id: str
    retry_handle_id: str
    exactly_once_submit: bool
    retry_reattached: bool
    recovery_point: str | None
    workflow_id_source: str | None


Layer0Submitter = Callable[[DurableSubmitTask], SubmitHandle]


@dataclass
class TemporalSubmitLifecycleSpike:
    client: TemporalSubmitClient
    config: TemporalSpikeConfig = field(default_factory=TemporalSpikeConfig)

    def start_or_attach(self, task: DurableSubmitTask) -> SubmitHandle:
        if not self.config.enabled:
            raise RuntimeError("temporal_spike_disabled")

        handle = self.client.start_workflow(
            self.config.workflow_name,
            task.payload,
            id=task.idempotency_key,
            task_queue=self.config.task_queue,
            id_conflict_policy=TEMPORAL_USE_EXISTING_POLICY,
        )
        handle_id = _extract_handle_id(handle, default=task.idempotency_key)
        created = _extract_created(handle)
        return SubmitHandle(
            system="temporal",
            task_id=task.task_id,
            handle_id=handle_id,
            created=created,
            reattached=created is False,
            recovery_point="workflow_started_or_existing",
            workflow_id_source="idempotency_key",
        )


def compare_submit_lifecycle(
    tasks: list[DurableSubmitTask],
    *,
    temporal: TemporalSubmitLifecycleSpike,
    layer0_submit: Layer0Submitter,
    code_surface_paths: list[Path] | None = None,
) -> dict[str, Any]:
    rows: list[SubmitComparison] = []
    for task in tasks:
        layer0_first = layer0_submit(task)
        layer0_retry = layer0_submit(task)
        rows.append(_compare(task, layer0_first, layer0_retry))

        temporal_first = temporal.start_or_attach(task)
        temporal_retry = temporal.start_or_attach(task)
        rows.append(_compare(task, temporal_first, temporal_retry))

    by_system: dict[str, dict[str, Any]] = {}
    for system in ("layer0", "temporal"):
        system_rows = [row for row in rows if row.system == system]
        by_system[system] = {
            "task_count": len(system_rows),
            "all_retry_handles_match": all(row.exactly_once_submit for row in system_rows),
            "all_retries_reattach": all(row.retry_reattached for row in system_rows),
        }

    report: dict[str, Any] = {
        "schema_version": "durable-execution-engine-spike/v1",
        "mode": "report_only",
        "default_runtime_changed": False,
        "temporal_conflict_policy": TEMPORAL_USE_EXISTING_POLICY,
        "workflow_id_source": "idempotency_key",
        "rows": [row.__dict__ for row in rows],
        "summary": by_system,
    }
    if code_surface_paths:
        report["line_count"] = {
            str(path): count_nonblank_lines(path) for path in code_surface_paths
        }
    return report


def count_nonblank_lines(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    return sum(1 for line in text.splitlines() if line.strip())


def _compare(
    task: DurableSubmitTask,
    first: SubmitHandle,
    retry: SubmitHandle,
) -> SubmitComparison:
    return SubmitComparison(
        task_id=task.task_id,
        system=first.system,
        first_handle_id=first.handle_id,
        retry_handle_id=retry.handle_id,
        exactly_once_submit=first.handle_id == retry.handle_id,
        retry_reattached=retry.reattached or retry.created is False,
        recovery_point=retry.recovery_point,
        workflow_id_source=retry.workflow_id_source,
    )


def _extract_handle_id(handle: Any, *, default: str) -> str:
    if isinstance(handle, dict):
        return str(
            handle.get("workflow_id")
            or handle.get("id")
            or handle.get("handle_id")
            or default
        )
    for attr in ("workflow_id", "id", "handle_id"):
        value = getattr(handle, attr, None)
        if value:
            return str(value)
    return default


def _extract_created(handle: Any) -> bool | None:
    if isinstance(handle, dict) and "created" in handle:
        return bool(handle["created"])
    value = getattr(handle, "created", None)
    if value is not None:
        return bool(value)
    return None
