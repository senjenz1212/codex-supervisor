from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.config import DurableExecutionCfg
from supervisor.durable_execution_engine_spike import (
    TEMPORAL_USE_EXISTING_POLICY,
    DurableSubmitTask,
    SubmitHandle,
    TemporalSpikeConfig,
    TemporalSubmitLifecycleSpike,
    compare_submit_lifecycle,
)
from supervisor.state import State


class FakeTemporalClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.handles: dict[str, dict[str, object]] = {}

    def start_workflow(
        self,
        workflow: str,
        payload: dict[str, object],
        *,
        id: str,
        task_queue: str,
        id_conflict_policy: str,
    ) -> dict[str, object]:
        self.calls.append({
            "workflow": workflow,
            "payload": payload,
            "id": id,
            "task_queue": task_queue,
            "id_conflict_policy": id_conflict_policy,
        })
        created = id not in self.handles
        self.handles.setdefault(id, {"workflow_id": id, "run_id": f"run-{id}"})
        return {**self.handles[id], "created": created}


def test_durable_execution_defaults_keep_hand_rolled_runtime() -> None:
    cfg = DurableExecutionCfg()

    assert cfg.engine == "hand_rolled"
    assert cfg.temporal_spike_enabled is False


def test_durable_execution_temporal_spike_engine_value_is_explicitly_flagged() -> None:
    cfg = DurableExecutionCfg(
        engine="temporal_spike",
        temporal_spike_enabled=True,
        temporal_task_queue="custom-spike-queue",
    )
    spike_cfg = TemporalSpikeConfig(
        enabled=cfg.temporal_spike_enabled,
        task_queue=cfg.temporal_task_queue,
    )

    assert cfg.engine == "temporal_spike"
    assert spike_cfg.enabled is True
    assert spike_cfg.task_queue == "custom-spike-queue"


def test_temporal_spike_is_disabled_until_flagged() -> None:
    spike = TemporalSubmitLifecycleSpike(
        FakeTemporalClient(),
        config=TemporalSpikeConfig(enabled=False),
    )

    with pytest.raises(RuntimeError, match="temporal_spike_disabled"):
        spike.start_or_attach(_task("disabled"))


def test_temporal_spike_uses_idempotency_key_as_workflow_id_with_use_existing() -> None:
    client = FakeTemporalClient()
    spike = TemporalSubmitLifecycleSpike(
        client,
        config=TemporalSpikeConfig(enabled=True, task_queue="spike-queue"),
    )
    task = _task("temporal-idempotency")

    first = spike.start_or_attach(task)
    retry = spike.start_or_attach(task)

    assert first.handle_id == task.idempotency_key
    assert retry.handle_id == first.handle_id
    assert retry.reattached is True
    assert client.calls == [
        {
            "workflow": "codex_supervisor.dual_agent_workflow_job",
            "payload": task.payload,
            "id": task.idempotency_key,
            "task_queue": "spike-queue",
            "id_conflict_policy": TEMPORAL_USE_EXISTING_POLICY,
        },
        {
            "workflow": "codex_supervisor.dual_agent_workflow_job",
            "payload": task.payload,
            "id": task.idempotency_key,
            "task_queue": "spike-queue",
            "id_conflict_policy": TEMPORAL_USE_EXISTING_POLICY,
        },
    ]


def test_spike_report_compares_temporal_submit_against_layer0_reservation(
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    client = FakeTemporalClient()
    spike = TemporalSubmitLifecycleSpike(
        client,
        config=TemporalSpikeConfig(enabled=True),
    )
    tasks = [_task(f"task-{index}") for index in range(3)]

    def layer0_submit(task: DurableSubmitTask) -> SubmitHandle:
        job_id = f"workflow-{task.task_id}"
        job_dir = tmp_path / ".handoff" / "workflow-jobs" / job_id
        row, created = state.reserve_dual_agent_workflow_job(
            job_id=job_id,
            run_id=task.run_id,
            task_id=task.task_id,
            cwd=str(tmp_path),
            status="submitted",
            request_path=str(job_dir / "request.json"),
            result_path=str(job_dir / "result.json"),
            log_path=str(job_dir / "worker.log"),
            idempotency_token=task.idempotency_key,
            request_payload_json=json.dumps(task.payload, sort_keys=True),
            config_path=str(tmp_path / "config.yaml"),
        )
        return SubmitHandle(
            system="layer0",
            task_id=task.task_id,
            handle_id=row["job_id"],
            created=created,
            reattached=not created,
            recovery_point=row["recovery_point"],
            workflow_id_source="idempotency_token",
        )

    report = compare_submit_lifecycle(
        tasks,
        temporal=spike,
        layer0_submit=layer0_submit,
        code_surface_paths=[
            Path("supervisor/durable_execution_engine_spike.py"),
        ],
    )

    assert report["schema_version"] == "durable-execution-engine-spike/v1"
    assert report["default_runtime_changed"] is False
    assert report["summary"]["layer0"] == {
        "task_count": 3,
        "all_retry_handles_match": True,
        "all_retries_reattach": True,
    }
    assert report["summary"]["temporal"] == {
        "task_count": 3,
        "all_retry_handles_match": True,
        "all_retries_reattach": True,
    }
    assert all(
        row["workflow_id_source"] in {"idempotency_token", "idempotency_key"}
        for row in report["rows"]
    )
    assert report["line_count"]["supervisor/durable_execution_engine_spike.py"] > 0


def _task(name: str) -> DurableSubmitTask:
    return DurableSubmitTask(
        task_id=name,
        run_id=f"run-{name}",
        idempotency_key=f"token-{name}",
        payload={
            "task_id": name,
            "run_id": f"run-{name}",
            "intent": "durable engine spike",
        },
    )
