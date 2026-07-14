"""Concrete repository-task executor for controlled A/B/C arms."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .agent_runtime import AgentRuntime, AgentTask
from .experiment_kernel import (
    ARM_EXECUTION_RECEIPT_SCHEMA_VERSION,
    EXECUTION_ENVIRONMENT_ATTESTATION_SCHEMA_VERSION,
    ISOLATION_ATTESTATION_SCHEMA_VERSION,
    Arm,
    ArmBudget,
    ArmExecution,
    ArmExecutionError,
    ArmExecutionReceipt,
    ExecutionEnvironmentAttestation,
    IsolationAttestation,
    TreatmentDescriptor,
)
from .runtime_cleanup import cancel_runtime_after_failure
from .task_environment import (
    FrozenTaskResult,
    TaskEnvironmentAdapter,
    TaskSpec,
    canonical_task_identity,
)


log = logging.getLogger(__name__)


_ISOLATION_ENV_KEYS = frozenset(
    {
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "SUPERVISOR_MEMORY_ROOT",
        "SUPERVISOR_LESSON_ROOT",
        "TMPDIR",
    }
)


@dataclass(frozen=True)
class RuntimeArmPlan:
    """Pinned runtime policy for one experiment arm."""

    model: str
    treatment: TreatmentDescriptor
    container_digest: str = ""
    network_policy: str = ""
    resource_limits: Mapping[str, Any] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)
    denied_paths: tuple[str, ...] = ()
    inherit_env: bool = False
    tools: tuple[str, ...] = ()
    execution_mode: str = ""
    operational: bool | None = None

    def __post_init__(self) -> None:
        if not str(self.model).strip():
            raise ValueError("runtime arm plan model must be non-empty")
        if not isinstance(self.treatment, TreatmentDescriptor):
            raise ValueError(
                "runtime arm plan treatment must be a TreatmentDescriptor"
            )
        if self.network_policy and self.network_policy not in {
            "disabled",
            "restricted",
            "enabled",
        }:
            raise ValueError("runtime arm plan network_policy is invalid")
        if self.inherit_env:
            raise ValueError(
                "experiment arm plans cannot inherit the operator environment"
            )
        if self.operational is not None and not isinstance(
            self.operational,
            bool,
        ):
            raise ValueError("runtime arm plan operational must be a bool")
        metadata_mode = str(
            self.runtime_metadata.get("execution_mode")
            or self.runtime_metadata.get("mode")
            or self.runtime_metadata.get("tracer_mode")
            or ""
        ).strip().casefold()
        requested_mode = str(self.execution_mode or "").strip().casefold()
        if self.operational is not None:
            bool_mode = "operational" if self.operational else "hermetic"
            if requested_mode and _normalize_execution_mode(
                requested_mode
            ) != bool_mode:
                raise ValueError(
                    "runtime arm plan execution_mode conflicts with operational"
                )
            requested_mode = bool_mode
        if not requested_mode and metadata_mode:
            requested_mode = metadata_mode
        mode = _normalize_execution_mode(
            requested_mode or "operational"
        )
        object.__setattr__(self, "execution_mode", mode)
        object.__setattr__(self, "operational", mode == "operational")
        reserved_environment = _ISOLATION_ENV_KEYS & set(self.environment)
        if reserved_environment:
            raise ValueError(
                "runtime arm plan cannot override isolation environment: "
                + ", ".join(sorted(reserved_environment))
            )
        object.__setattr__(
            self,
            "resource_limits",
            MappingProxyType(dict(self.resource_limits)),
        )
        object.__setattr__(
            self,
            "environment",
            MappingProxyType(dict(self.environment)),
        )
        object.__setattr__(
            self,
            "runtime_metadata",
            MappingProxyType(dict(self.runtime_metadata)),
        )
        object.__setattr__(
            self,
            "denied_paths",
            tuple(str(path) for path in self.denied_paths),
        )
        raw_tools: Any = self.tools or self.runtime_metadata.get("tools") or ()
        if isinstance(raw_tools, str):
            raw_tools = (raw_tools,)
        if not isinstance(raw_tools, (list, tuple)):
            raise ValueError("runtime arm plan tools must be a sequence")
        tools = tuple(
            str(tool).strip()
            for tool in raw_tools
            if str(tool).strip()
        )
        if len(tools) != len(tuple(raw_tools)):
            raise ValueError("runtime arm plan tools must be non-empty strings")
        object.__setattr__(self, "tools", tools)

    def instruction(self, task: TaskSpec) -> str:
        return self.treatment.render_instruction(task.problem_statement)


class RepositoryArmExecutor:
    """Run each arm in a fresh checkout and freeze its patch before grading."""

    def __init__(
        self,
        *,
        task_environment: TaskEnvironmentAdapter,
        runtimes: Mapping[Arm, AgentRuntime],
        plans: Mapping[Arm, RuntimeArmPlan],
    ) -> None:
        missing_runtimes = set(Arm) - set(runtimes)
        missing_plans = set(Arm) - set(plans)
        if missing_runtimes or missing_plans:
            raise ValueError(
                "repository arm executor requires a runtime and plan for every arm"
            )
        self.task_environment = task_environment
        self.runtimes = dict(runtimes)
        self.plans = dict(plans)
        self._plan_fingerprints = {
            arm: _plan_fingerprint(self.runtimes[arm], self.plans[arm])
            for arm in Arm
        }
        treatment_hashes = {
            arm: self.plans[arm].treatment.treatment_hash
            for arm in Arm
        }
        if len(set(treatment_hashes.values())) != len(Arm):
            raise ValueError(
                "A/B/C runtime treatment hashes must all differ"
            )
        self._compute_resource_fingerprints = {
            arm: _compute_resource_fingerprint(
                self.runtimes[arm],
                self.plans[arm],
            )
            for arm in Arm
        }
        self._runtime_identity_owners: dict[
            tuple[str, str, str],
            tuple[str, int],
        ] = {}
        self._task_runtime_manifests: dict[
            str,
            dict[Arm, Mapping[str, Any]],
        ] = {}
        self._verifier_denied_paths: dict[str, tuple[str, ...]] = {}
        if (
            self._compute_resource_fingerprints[Arm.B]
            != self._compute_resource_fingerprints[Arm.C]
        ):
            raise ValueError(
                "arms B and C must have identical runtime/model/container/"
                "network/resource plans"
            )

    def bind_verifier(self, *, task: TaskSpec, verifier: Any) -> None:
        """Bind verifier-owned hidden paths before any arm starts.

        Hidden material belongs to the verifier boundary, not to individual
        call sites.  The experiment kernel invokes this hook before execution
        so every arm receives the same deny policy automatically.
        """

        raw_paths = getattr(verifier, "protected_paths", ())
        if callable(raw_paths):
            raw_paths = raw_paths()
        if isinstance(raw_paths, (str, bytes)) or not isinstance(
            raw_paths,
            (list, tuple),
        ):
            raise ValueError(
                "verifier protected_paths must be a path sequence"
            )
        resolved: list[str] = []
        seen: set[str] = set()
        for raw_path in raw_paths:
            text = str(raw_path or "").strip()
            if not text:
                raise ValueError(
                    "verifier protected_paths cannot contain empty paths"
                )
            path = str(Path(text).expanduser().resolve(strict=False))
            if path in seen:
                continue
            seen.add(path)
            resolved.append(path)
        task_hash = task.spec_hash
        paths = tuple(resolved)
        existing = self._verifier_denied_paths.get(task_hash)
        if existing is not None and existing != paths:
            raise ValueError(
                "verifier protected paths changed after task binding"
            )
        self._verifier_denied_paths[task_hash] = paths

    async def execute(
        self,
        *,
        arm: Arm,
        task: TaskSpec,
        budget: ArmBudget,
        assignment_id: str,
    ) -> ArmExecution:
        self._assert_plans_frozen()
        effective_plans = self._effective_plans_for_task(task)
        runtime_manifests = self._runtime_manifests_for_task(
            task,
            plans=effective_plans,
        )
        runtime = self.runtimes[arm]
        plan = effective_plans[arm]
        runtime_manifest = runtime_manifests[arm]
        execution_plan = _effective_execution_plan(
            runtime=runtime,
            plan=plan,
            task=task,
            budget=budget,
        )
        execution_id = uuid.uuid4().hex
        attempts = 0
        total_cost = 0.0
        total_token_usage: dict[str, Any] = {}
        started_at = time.monotonic()
        attempt_records: list[dict[str, Any]] = []
        last_error: Exception | None = None

        for attempt_index in range(max(1, int(budget.max_retries) + 1)):
            if attempt_index > 0 and not _retry_budget_available(
                budget,
                total_cost=total_cost,
                total_tokens=_total_tokens(total_token_usage),
                elapsed_s=time.monotonic() - started_at,
            ):
                break
            attempts += 1
            attempt_started = time.monotonic()
            materialized = None
            run_result = None
            handle = None
            handle_active = False
            streamed_events = []
            isolation: dict[str, str] = {}
            execution_task_id = ""
            environment_attestation: ExecutionEnvironmentAttestation | None = (
                None
            )
            try:
                if time.monotonic() - started_at >= float(budget.timeout_s):
                    raise TimeoutError("arm exhausted its ex-ante time ceiling")
                materialized = await self.task_environment.materialize(task)
                isolation = _create_isolation_roots(
                    materialized.workspace,
                    execution_id=execution_id,
                    attempt_index=attempt_index,
                )
                execution_task_id = _execution_task_id(
                    task_id=task.task_id,
                    arm=arm,
                    execution_id=execution_id,
                    attempt_index=attempt_index,
                )
                isolated_environment = _isolated_environment(
                    plan.environment,
                    isolation=isolation,
                )
                remaining_timeout_s = (
                    float(budget.timeout_s)
                    - (time.monotonic() - started_at)
                )
                if remaining_timeout_s <= 0:
                    raise TimeoutError("arm exhausted its ex-ante time ceiling")
                metadata = {
                    **dict(plan.runtime_metadata),
                    "tools": list(plan.tools),
                    "execution_mode": plan.execution_mode,
                    "max_tokens": int(budget.max_tokens),
                    "max_budget_usd": float(budget.max_cost_usd),
                    "experiment": {
                        "arm": arm.value,
                        "assignment_id": assignment_id,
                        "treatment_hash": plan.treatment.treatment_hash,
                        "arm_adapter": plan.treatment.arm_adapter,
                        "entrypoint": plan.treatment.entrypoint,
                        "treatment_descriptor": plan.treatment.to_dict(),
                        "plan_fingerprint": execution_plan[
                            "plan_fingerprint"
                        ],
                        "compute_resource_hash": execution_plan[
                            "compute_resource_hash"
                        ],
                        "attempt_index": attempt_index,
                        "execution_id": execution_id,
                        "execution_task_id": execution_task_id,
                        "isolation": isolation,
                        "budget": budget.to_dict(),
                        "runtime_plan": execution_plan,
                        "runtime_manifest_sha256": runtime_manifest[
                            "manifest_sha256"
                        ],
                    },
                    "filesystem_isolation": {
                        "mode": "workspace_only",
                        "workspace": str(materialized.workspace),
                        "deny_paths": [
                            str(Path(path).expanduser().resolve())
                            for path in plan.denied_paths
                        ],
                        "network_policy": execution_plan["network_policy"],
                        "required": True,
                    },
                }
                agent_task = AgentTask(
                    task_id=execution_task_id,
                    instruction=plan.instruction(task),
                    cwd=materialized.workspace,
                    model=plan.model,
                    timeout_s=remaining_timeout_s,
                    env=isolated_environment,
                    inherit_env=False,
                    metadata=metadata,
                )
                actual_manifest = _runtime_manifest_for_agent_task(
                    runtime,
                    agent_task,
                    operational=bool(plan.operational),
                )
                if dict(actual_manifest) != dict(runtime_manifest):
                    raise RuntimeError(
                        "runtime manifest changed between preflight and launch"
                    )
                handle = await runtime.start(agent_task)
                handle_active = True
                _validate_run_handle(
                    handle,
                    expected_task_id=execution_task_id,
                    runtime=runtime,
                )
                self._claim_runtime_identity(
                    assignment_id=assignment_id,
                    owner=(execution_id, attempt_index),
                    kind="run_id",
                    value=handle.run_id,
                )
                self._claim_runtime_identity(
                    assignment_id=assignment_id,
                    owner=(execution_id, attempt_index),
                    kind="session_id",
                    value=handle.session_id,
                )
                streamed_events = [event async for event in runtime.stream(handle)]
                run_result = await runtime.collect(handle)
                handle_active = False
                total_cost += float(run_result.cost_usd)
                total_token_usage = _merge_token_usage(
                    total_token_usage,
                    run_result.token_usage,
                )
                attempt_record = {
                    "attempt_index": attempt_index,
                    "execution_id": execution_id,
                    "execution_task_id": execution_task_id,
                    "run_id": run_result.run_id,
                    "session_id": run_result.session_id,
                    "isolation": dict(isolation),
                    "status": run_result.status,
                    "cost_usd": float(run_result.cost_usd),
                    "token_usage": dict(run_result.token_usage),
                    "latency_ms": max(
                        0,
                        int((time.monotonic() - attempt_started) * 1000),
                    ),
                }
                raw_environment_attestation = run_result.metadata.get(
                    "execution_environment_attestation"
                )
                if isinstance(raw_environment_attestation, Mapping):
                    attempt_record[
                        "execution_environment_attestation"
                    ] = json.loads(
                        json.dumps(
                            dict(raw_environment_attestation),
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                    )
                attempt_records.append(attempt_record)
                _validate_run_result_provenance(
                    run_result,
                    runtime=runtime,
                    plan=plan,
                    expected_task_id=execution_task_id,
                    expected_run_id=handle.run_id,
                )
                environment_attestation = _execution_environment_attestation(
                    result_metadata=run_result.metadata,
                    runtime=runtime,
                    plan=plan,
                    task=task,
                    execution_plan=execution_plan,
                    execution_id=execution_id,
                )
                self._claim_runtime_identity(
                    assignment_id=assignment_id,
                    owner=(execution_id, attempt_index),
                    kind="session_id",
                    value=run_result.session_id,
                )
                ceiling_failure = _budget_ceiling_failure(
                    budget,
                    total_cost=total_cost,
                    total_tokens=_total_tokens(total_token_usage),
                    elapsed_s=time.monotonic() - started_at,
                )
                if ceiling_failure:
                    raise ArmExecutionError(
                        ceiling_failure,
                        attempts=attempts,
                        cost_usd=total_cost,
                        latency_ms=_elapsed_ms(started_at),
                        token_usage=total_token_usage,
                        attempt_records=tuple(attempt_records),
                        failure_classification="budget_exceeded",
                        metadata={"runtime_plan": execution_plan},
                    )
                if run_result.status != "completed":
                    raise RuntimeError(
                        f"agent runtime ended with status={run_result.status}"
                    )
                frozen = await self.task_environment.collect_patch(
                    materialized,
                    run_result_hash=run_result.result_hash,
                    output=run_result.output,
                )
                frozen = _bind_frozen_result_to_launch(
                    frozen,
                    arm=arm,
                    assignment_id=assignment_id,
                    treatment_hash=plan.treatment.treatment_hash,
                )
                if not frozen.patch.strip():
                    raise ArmExecutionError(
                        "arm produced an empty patch",
                        attempts=attempts,
                        cost_usd=total_cost,
                        latency_ms=_elapsed_ms(started_at),
                        token_usage=total_token_usage,
                        attempt_records=tuple(attempt_records),
                        failure_classification="empty_patch",
                        metadata={"runtime_plan": execution_plan},
                    )
                elapsed_s = time.monotonic() - started_at
                if elapsed_s > float(budget.timeout_s):
                    raise ArmExecutionError(
                        "arm exceeded its ex-ante time ceiling",
                        attempts=attempts,
                        cost_usd=total_cost,
                        latency_ms=_elapsed_ms(started_at),
                        token_usage=total_token_usage,
                        attempt_records=tuple(attempt_records),
                        failure_classification="budget_exceeded",
                        metadata={"runtime_plan": execution_plan},
                    )
                if handle is None or environment_attestation is None:
                    raise RuntimeError(
                        "completed arm lacks runtime/environment attestation"
                    )
                latency_ms = _elapsed_ms(started_at)
                result_id = uuid.uuid4().hex
                isolation_attestation = IsolationAttestation(
                    isolation_id=_canonical_hash({
                        "execution_id": execution_id,
                        "workspace": str(materialized.workspace),
                        "session_id": run_result.session_id,
                        "isolation": isolation,
                    }),
                    workspace_id=str(
                        getattr(materialized, "materialization_id", "")
                        or materialized.workspace
                    ),
                    session_id=run_result.session_id,
                    cache_namespace=isolation["cache"],
                    memory_namespace=isolation["memory"],
                    lesson_namespace=isolation["lessons"],
                    enforced=(
                        handle.capabilities.get("filesystem_isolation")
                        is True
                    ),
                    schema_version=ISOLATION_ATTESTATION_SCHEMA_VERSION,
                )
                receipt = ArmExecutionReceipt(
                    execution_id=execution_id,
                    result_id=result_id,
                    assignment_id=assignment_id,
                    task_id=task.task_id,
                    canonical_task_id=canonical_task_identity(task),
                    task_spec_hash=task.spec_hash,
                    arm=arm,
                    treatment_hash=plan.treatment.treatment_hash,
                    plan_fingerprint=execution_plan[
                        "plan_fingerprint"
                    ],
                    compute_resource_hash=execution_plan[
                        "compute_resource_hash"
                    ],
                    frozen_result_hash=frozen.result_hash,
                    attempts=attempts,
                    cost_usd=total_cost,
                    latency_ms=latency_ms,
                    token_usage=dict(total_token_usage),
                    attempt_records=tuple(attempt_records),
                    isolation=isolation_attestation,
                    environment=environment_attestation,
                    runtime_manifest=dict(runtime_manifest),
                    schema_version=ARM_EXECUTION_RECEIPT_SCHEMA_VERSION,
                )
                return ArmExecution(
                    frozen_result=frozen,
                    attempts=attempts,
                    cost_usd=total_cost,
                    latency_ms=latency_ms,
                    metadata={
                        "arm": arm.value,
                        "assignment_id": assignment_id,
                        "execution_id": execution_id,
                        "execution_task_id": execution_task_id,
                        "isolation": isolation,
                        "runtime": run_result.runtime,
                        "resolved_model": run_result.resolved_model,
                        "runtime_event_count": len(streamed_events),
                        "run_id": run_result.run_id,
                        "session_id": run_result.session_id,
                        "token_usage": dict(total_token_usage),
                        "attempt_records": tuple(attempt_records),
                        "runtime_plan": execution_plan,
                        "launch_metadata": {
                            "arm": arm.value,
                            "assignment_id": assignment_id,
                            "treatment_hash": (
                                plan.treatment.treatment_hash
                            ),
                            "plan_fingerprint": execution_plan[
                                "plan_fingerprint"
                            ],
                            "compute_resource_hash": execution_plan[
                                "compute_resource_hash"
                            ],
                            "arm_adapter": plan.treatment.arm_adapter,
                            "entrypoint": plan.treatment.entrypoint,
                        },
                        "runtime_manifest": dict(runtime_manifest),
                        "execution_environment_attestation": (
                            environment_attestation.to_dict()
                        ),
                        "execution_receipt": receipt.to_dict(),
                    },
                    receipt=receipt,
                )
            except BaseException as caught:
                if handle is not None and handle_active:
                    await cancel_runtime_after_failure(
                        runtime,
                        handle,
                        logger=log,
                    )
                    handle_active = False
                if isinstance(caught, ArmExecutionError):
                    raise
                if not isinstance(caught, Exception):
                    raise
                exc = caught
                last_error = exc
                if run_result is None:
                    observed_cost = float(getattr(exc, "cost_usd", 0.0))
                    observed_tokens = dict(
                        getattr(exc, "token_usage", {}) or {}
                    )
                    total_cost += observed_cost
                    total_token_usage = _merge_token_usage(
                        total_token_usage,
                        observed_tokens,
                    )
                    exception_records = tuple(
                        dict(record)
                        for record in (
                            getattr(exc, "attempt_records", ()) or ()
                        )
                    )
                    if exception_records:
                        attempt_records.extend(exception_records)
                    else:
                        attempt_records.append(
                            {
                                "attempt_index": attempt_index,
                                "execution_id": execution_id,
                                "execution_task_id": execution_task_id,
                                "run_id": (
                                    f"prestart-{execution_id}-{attempt_index}"
                                ),
                                "session_id": (
                                    f"prestart-{execution_id}-{attempt_index}"
                                ),
                                "isolation": dict(isolation),
                                "status": "failed",
                                "cost_usd": observed_cost,
                                "token_usage": observed_tokens,
                                "latency_ms": max(
                                    0,
                                    int(
                                        (
                                            time.monotonic()
                                            - attempt_started
                                        )
                                        * 1000
                                    ),
                                ),
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                else:
                    attempt_records[-1]["error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
                if (
                    attempt_index < int(budget.max_retries)
                    and _retry_budget_available(
                        budget,
                        total_cost=total_cost,
                        total_tokens=_total_tokens(total_token_usage),
                        elapsed_s=time.monotonic() - started_at,
                    )
                ):
                    continue
                classification = (
                    "budget_exhausted"
                    if attempt_index < int(budget.max_retries)
                    else str(
                        getattr(exc, "failure_classification", "")
                        or "treatment_execution_failure"
                    )
                )
                raise ArmExecutionError(
                    f"{type(exc).__name__}: {exc}",
                    attempts=attempts,
                    cost_usd=total_cost,
                    latency_ms=_elapsed_ms(started_at),
                    token_usage=total_token_usage,
                    attempt_records=tuple(attempt_records),
                    failure_classification=classification,
                    metadata={"runtime_plan": execution_plan},
                ) from exc
            finally:
                if materialized is not None:
                    await self.task_environment.teardown(materialized)

        raise ArmExecutionError(
            "arm execution exhausted without a result",
            attempts=attempts,
            cost_usd=total_cost,
            latency_ms=_elapsed_ms(started_at),
            token_usage=total_token_usage,
            attempt_records=tuple(attempt_records),
            failure_classification="budget_exhausted",
            metadata={"runtime_plan": execution_plan},
        ) from last_error

    def _claim_runtime_identity(
        self,
        *,
        assignment_id: str,
        owner: tuple[str, int],
        kind: str,
        value: str,
    ) -> None:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"runtime {kind} must be non-empty")
        key = (assignment_id, kind, normalized)
        existing_owner = self._runtime_identity_owners.get(key)
        if existing_owner is not None and existing_owner != owner:
            raise ValueError(
                f"runtime {kind} was reused across isolated arm attempts"
            )
        self._runtime_identity_owners[key] = owner

    def _assert_plans_frozen(self) -> None:
        current = {
            arm: _plan_fingerprint(self.runtimes[arm], self.plans[arm])
            for arm in Arm
        }
        if current != self._plan_fingerprints:
            raise RuntimeError("runtime arm plan changed after executor construction")

    def _effective_plans_for_task(
        self,
        task: TaskSpec,
    ) -> dict[Arm, RuntimeArmPlan]:
        verifier_paths = self._verifier_denied_paths.get(task.spec_hash, ())
        effective: dict[Arm, RuntimeArmPlan] = {}
        for arm, plan in self.plans.items():
            denied_paths = tuple(
                dict.fromkeys(
                    (
                        *(
                            str(
                                Path(path)
                                .expanduser()
                                .resolve(strict=False)
                            )
                            for path in plan.denied_paths
                        ),
                        *verifier_paths,
                    )
                )
            )
            effective[arm] = (
                plan
                if denied_paths == plan.denied_paths
                else replace(plan, denied_paths=denied_paths)
            )
        return effective

    def _runtime_manifests_for_task(
        self,
        task: TaskSpec,
        *,
        plans: Mapping[Arm, RuntimeArmPlan],
    ) -> dict[Arm, Mapping[str, Any]]:
        manifests = {
            arm: _runtime_manifest(
                self.runtimes[arm],
                plan=plans[arm],
                task=task,
                cwd=Path(task.repo),
            )
            for arm in Arm
        }
        comparisons = {
            arm: _canonical_hash({
                "runtime_manifest": dict(manifests[arm]),
                "frozen_plan": _compute_match_plan(
                    plans[arm],
                    task=task,
                ),
            })
            for arm in (Arm.B, Arm.C)
        }
        if comparisons[Arm.B] != comparisons[Arm.C]:
            raise ValueError(
                "arms B and C must have identical complete runtime manifests "
                "and frozen compute plans"
            )
        task_hash = task.spec_hash
        previous = self._task_runtime_manifests.get(task_hash)
        if previous is not None:
            if any(
                dict(previous[arm]) != dict(manifests[arm])
                for arm in Arm
            ):
                raise RuntimeError(
                    "runtime manifest changed after task preflight"
                )
        else:
            self._task_runtime_manifests[task_hash] = dict(manifests)
        return manifests


def _bind_frozen_result_to_launch(
    frozen: FrozenTaskResult,
    *,
    arm: Arm,
    assignment_id: str,
    treatment_hash: str,
) -> FrozenTaskResult:
    return FrozenTaskResult.create(
        task_id=frozen.task_id,
        task_family=frozen.task_family,
        task_spec_hash=frozen.task_spec_hash,
        run_result_hash=frozen.run_result_hash,
        patch=frozen.patch,
        output=frozen.output,
        metadata={
            **dict(frozen.metadata),
            "harness_arm": arm.value,
            "assignment_id": assignment_id,
            "experiment": {
                "treatment": arm.value,
                "treatment_hash": treatment_hash,
            },
        },
        frozen_at_ms=frozen.frozen_at_ms,
    )


def _create_isolation_roots(
    workspace: Path,
    *,
    execution_id: str,
    attempt_index: int,
) -> dict[str, str]:
    root = (
        workspace
        / ".git"
        / "harness-v1-isolation"
        / execution_id
        / f"attempt-{attempt_index}"
    )
    paths = {
        "root": root,
        "home": root / "home",
        "config": root / "config",
        "cache": root / "cache",
        "memory": root / "memory",
        "lessons": root / "lessons",
        "tmp": root / "tmp",
        "codex_home": root / "config" / "codex",
        "claude_config": root / "config" / "claude",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return {key: str(path.resolve()) for key, path in paths.items()}


def _isolated_environment(
    configured: Mapping[str, str],
    *,
    isolation: Mapping[str, str],
) -> dict[str, str]:
    return {
        **{str(key): str(value) for key, value in configured.items()},
        "HOME": isolation["home"],
        "XDG_CONFIG_HOME": isolation["config"],
        "XDG_CACHE_HOME": isolation["cache"],
        "CODEX_HOME": isolation["codex_home"],
        "CLAUDE_CONFIG_DIR": isolation["claude_config"],
        "SUPERVISOR_MEMORY_ROOT": isolation["memory"],
        "SUPERVISOR_LESSON_ROOT": isolation["lessons"],
        "TMPDIR": isolation["tmp"],
    }


def _execution_task_id(
    *,
    task_id: str,
    arm: Arm,
    execution_id: str,
    attempt_index: int,
) -> str:
    return (
        f"{task_id}::arm={arm.name.lower()}::"
        f"execution={execution_id}::attempt={attempt_index}"
    )


def _validate_run_handle(
    handle,
    *,
    expected_task_id: str,
    runtime: AgentRuntime,
) -> None:
    if handle.task_id != expected_task_id:
        raise ValueError("runtime handle task_id differs from isolated execution")
    if handle.runtime != str(getattr(runtime, "kind", "") or ""):
        raise ValueError("runtime handle kind differs from frozen runtime plan")
    if not str(handle.run_id or "").strip():
        raise ValueError("runtime handle run_id must be non-empty")
    if not str(handle.session_id or "").strip():
        raise ValueError("runtime handle session_id must be non-empty")
    if handle.capabilities.get("filesystem_isolation") is not True:
        raise ValueError(
            "runtime handle must attest enforced filesystem isolation"
        )


def _plan_fingerprint(
    runtime: AgentRuntime,
    plan: RuntimeArmPlan,
) -> str:
    payload = {
        "runtime": _runtime_identity(runtime),
        "treatment": plan.treatment.to_dict(),
        "model": plan.model,
        "container_digest": plan.container_digest,
        "network_policy": plan.network_policy,
        "resource_limits": dict(plan.resource_limits),
        "environment": dict(plan.environment),
        "runtime_metadata": dict(plan.runtime_metadata),
        "inherit_env": bool(plan.inherit_env),
        "denied_paths": list(plan.denied_paths),
        "tools": list(plan.tools),
        "execution_mode": plan.execution_mode,
        "operational": bool(plan.operational),
    }
    return _canonical_hash(payload)


def _compute_resource_fingerprint(
    runtime: AgentRuntime,
    plan: RuntimeArmPlan,
) -> str:
    return _canonical_hash({
        "runtime": _runtime_identity(runtime),
        "model": plan.model,
        "container_digest": plan.container_digest,
        "network_policy": plan.network_policy,
        "resource_limits": dict(plan.resource_limits),
        "environment": dict(plan.environment),
        "runtime_metadata": dict(plan.runtime_metadata),
        "inherit_env": bool(plan.inherit_env),
        "denied_paths": list(plan.denied_paths),
        "tools": list(plan.tools),
        "execution_mode": plan.execution_mode,
        "operational": bool(plan.operational),
    })


def _runtime_identity(runtime: AgentRuntime) -> Mapping[str, str]:
    transport = getattr(runtime, "_transport", None)
    return {
        "kind": str(getattr(runtime, "kind", "") or ""),
        "implementation": (
            f"{type(runtime).__module__}.{type(runtime).__qualname__}"
        ),
        "binary": str(getattr(runtime, "_binary", "") or ""),
        "transport": (
            ""
            if transport is None
            else f"{type(transport).__module__}.{type(transport).__qualname__}"
        ),
    }


def _runtime_manifest(
    runtime: AgentRuntime,
    *,
    plan: RuntimeArmPlan,
    task: TaskSpec,
    cwd: Path,
) -> Mapping[str, Any]:
    manifest_task = AgentTask(
        task_id=f"runtime-manifest::{canonical_task_identity(task)}",
        instruction="runtime manifest preflight",
        cwd=cwd,
        model=plan.model,
        timeout_s=1,
        env={},
        inherit_env=False,
        metadata={
            **dict(plan.runtime_metadata),
            "tools": list(plan.tools),
            "execution_mode": plan.execution_mode,
            "resource_plan": _compute_match_plan(plan, task=task),
        },
    )
    return _runtime_manifest_for_agent_task(
        runtime,
        manifest_task,
        operational=bool(plan.operational),
    )


def _runtime_manifest_for_agent_task(
    runtime: AgentRuntime,
    task: AgentTask,
    *,
    operational: bool,
) -> Mapping[str, Any]:
    provider = getattr(runtime, "runtime_manifest", None)
    if callable(provider):
        raw = provider(task)
        if not isinstance(raw, Mapping):
            raise ValueError("runtime_manifest must return a mapping")
        manifest = json.loads(
            json.dumps(
                dict(raw),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    else:
        if operational:
            raise ValueError(
                "operational runtime must provide runtime_manifest(task)"
            )
        body = {
            "schema_version": "supervisor-agent-runtime-manifest/v1",
            "kind": str(getattr(runtime, "kind", "") or ""),
            "implementation": (
                f"{type(runtime).__module__}.{type(runtime).__qualname__}"
            ),
            "provider_route": {
                "provider": "non-operational-fixture",
                "route_kind": "fixture",
                "endpoint": "not-executed",
                "model_request": task.model,
                "complete": False,
            },
            "binary": {
                "requested": "",
                "resolved_path": "",
                "sha256": "",
                "complete": False,
            },
            "transport": {
                "implementation": (
                    f"{type(runtime).__module__}.{type(runtime).__qualname__}"
                ),
                "configuration": {"mode": "non-operational-fixture"},
                "configuration_sha256": _canonical_hash(
                    {"mode": "non-operational-fixture"}
                ),
                "complete": False,
            },
            "tools": task.metadata.get("tools"),
            "complete": False,
        }
        manifest = {
            **body,
            "manifest_sha256": _canonical_hash(body),
        }
    _validate_runtime_manifest(
        manifest,
        operational=operational,
    )
    return MappingProxyType(dict(manifest))


def _validate_runtime_manifest(
    manifest: Mapping[str, Any],
    *,
    operational: bool,
) -> None:
    manifest_hash = str(manifest.get("manifest_sha256") or "")
    body = {
        str(key): value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    if (
        len(manifest_hash) != 64
        or any(character not in "0123456789abcdef" for character in manifest_hash)
        or _canonical_hash(body) != manifest_hash
    ):
        raise ValueError("runtime manifest hash is invalid")
    if not operational:
        return
    if manifest.get("schema_version") != (
        "supervisor-agent-runtime-manifest/v1"
    ):
        raise ValueError("operational runtime manifest schema is invalid")
    if manifest.get("complete") is not True:
        raise ValueError("operational runtime manifest must be complete")
    route = manifest.get("provider_route")
    binary = manifest.get("binary")
    transport = manifest.get("transport")
    if not all(
        isinstance(value, Mapping)
        for value in (route, binary, transport)
    ):
        raise ValueError("operational runtime manifest sections are missing")
    for key in ("provider", "route_kind", "endpoint", "model_request"):
        if not str(route.get(key) or "").strip():
            raise ValueError(
                f"operational runtime provider route lacks {key}"
            )
    if route.get("complete") is not True:
        raise ValueError("operational runtime provider route is incomplete")
    binary_digest = str(binary.get("sha256") or "")
    if (
        binary.get("complete") is not True
        or len(binary_digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in binary_digest
        )
    ):
        raise ValueError("operational runtime binary digest is incomplete")
    configuration = transport.get("configuration")
    if (
        transport.get("complete") is not True
        or not str(transport.get("implementation") or "").strip()
        or not isinstance(configuration, Mapping)
        or not configuration
        or _canonical_hash(dict(configuration))
        != str(transport.get("configuration_sha256") or "")
    ):
        raise ValueError(
            "operational runtime transport configuration is incomplete"
        )
    tools = manifest.get("tools")
    if (
        not isinstance(tools, (list, tuple))
        or any(
            not isinstance(tool, str) or not tool.strip()
            for tool in tools
        )
    ):
        raise ValueError(
            "operational runtime tools must be an explicit string sequence"
        )


def _compute_match_plan(
    plan: RuntimeArmPlan,
    *,
    task: TaskSpec,
) -> dict[str, Any]:
    container_digest = str(plan.container_digest or task.image_digest).strip()
    network_policy = str(plan.network_policy or task.network_policy).strip()
    resource_limits = dict(task.resource_limits)
    for key, value in plan.resource_limits.items():
        if key in resource_limits and resource_limits[key] != value:
            raise ValueError(
                f"runtime plan resource limit {key} differs from TaskSpec"
            )
        resource_limits[key] = value
    return {
        "model": plan.model,
        "container_digest": container_digest,
        "architecture": task.architecture,
        "os_name": task.os_name,
        "network_policy": network_policy,
        "resource_limits": resource_limits,
        "environment": dict(plan.environment),
        "runtime_metadata": dict(plan.runtime_metadata),
        "denied_paths": list(plan.denied_paths),
        "inherit_env": bool(plan.inherit_env),
        "tools": list(plan.tools),
        "execution_mode": plan.execution_mode,
    }


def _execution_environment_attestation(
    *,
    result_metadata: Mapping[str, Any],
    runtime: AgentRuntime,
    plan: RuntimeArmPlan,
    task: TaskSpec,
    execution_plan: Mapping[str, Any],
    execution_id: str,
) -> ExecutionEnvironmentAttestation:
    raw = (
        result_metadata.get("execution_environment_attestation")
        or result_metadata.get("execution_attestation")
        or result_metadata.get("resource_attestation")
    )
    if plan.operational:
        if not isinstance(raw, Mapping):
            raise ValueError(
                "operational backend did not attest execution environment pins"
            )
        transport = getattr(runtime, "_transport", None)
        if (
            transport is not None
            and str(raw.get("attestation_source") or "")
            != "runtime_transport"
        ):
            raise ValueError(
                "concrete command runtime attestation was not transport-derived"
            )
        raw_unmet_pins = raw.get("unmet_pins")
        if str(raw.get("attestation_source") or "") == "runtime_transport":
            if (
                not isinstance(raw_unmet_pins, (list, tuple))
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in raw_unmet_pins
                )
            ):
                raise ValueError(
                    "runtime transport attestation lacks explicit pin checks"
                )
            attestation_id = str(raw.get("attestation_id") or "")
            attestation_body = {
                str(key): value
                for key, value in raw.items()
                if key != "attestation_id"
            }
            if (
                len(attestation_id) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in attestation_id
                )
                or _canonical_hash(attestation_body) != attestation_id
            ):
                raise ValueError(
                    "runtime transport attestation hash is invalid"
                )
        unmet_pins = [
            str(value).strip()
            for value in (
                raw_unmet_pins
                if isinstance(raw_unmet_pins, (list, tuple))
                else ()
            )
            if str(value).strip()
        ]
        if unmet_pins:
            raise ValueError(
                "operational backend did not enforce execution environment "
                "pins: "
                + ", ".join(unmet_pins)
            )
        attestation = ExecutionEnvironmentAttestation.from_mapping(raw)
    else:
        if isinstance(raw, Mapping):
            claimed = ExecutionEnvironmentAttestation.from_mapping(raw)
            if claimed.operational or claimed.enforced:
                raise ValueError(
                    "non-operational fixture must not claim operational attestation"
                )
        body = {
            "mode": "hermetic",
            "backend": str(getattr(runtime, "kind", "") or "fixture"),
            "execution_id": execution_id,
            "task_spec_hash": task.spec_hash,
            "execution_plan_hash": execution_plan[
                "compute_resource_hash"
            ],
        }
        attestation = ExecutionEnvironmentAttestation(
            attestation_id=_canonical_hash(body),
            mode="hermetic",
            backend=body["backend"],
            image_digest=task.image_digest,
            architecture=task.architecture,
            os_name=task.os_name,
            network_policy=task.network_policy,
            resource_limits=dict(execution_plan["resource_limits"]),
            enforced=False,
            schema_version=(
                EXECUTION_ENVIRONMENT_ATTESTATION_SCHEMA_VERSION
            ),
        )
    if (
        attestation.schema_version
        != EXECUTION_ENVIRONMENT_ATTESTATION_SCHEMA_VERSION
    ):
        raise ValueError("execution environment attestation schema is invalid")
    expected_mode = "operational" if plan.operational else "hermetic"
    if attestation.mode != expected_mode:
        raise ValueError("execution environment attestation mode is invalid")
    if attestation.enforced is not bool(plan.operational):
        raise ValueError(
            "execution environment attestation enforcement is invalid"
        )
    if (
        attestation.image_digest != task.image_digest
        or attestation.architecture != task.architecture
        or attestation.os_name != task.os_name
        or attestation.network_policy != task.network_policy
        or dict(attestation.resource_limits)
        != dict(execution_plan["resource_limits"])
    ):
        raise ValueError(
            "backend execution attestation does not match frozen task/resource pins"
        )
    if (
        not str(attestation.attestation_id).strip()
        or not str(attestation.backend).strip()
    ):
        raise ValueError(
            "execution environment attestation identity is incomplete"
        )
    return attestation


def _normalize_execution_mode(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized == "operational":
        return "operational"
    if normalized in {
        "fixture",
        "hermetic",
        "non-operational",
        "non_operational",
        "test",
    }:
        return "hermetic"
    raise ValueError(f"runtime arm plan execution_mode is invalid: {value}")


def _effective_execution_plan(
    *,
    runtime: AgentRuntime,
    plan: RuntimeArmPlan,
    task: TaskSpec,
    budget: ArmBudget,
) -> dict[str, Any]:
    runtime_kind = str(getattr(runtime, "kind", "") or "").strip()
    if not runtime_kind:
        raise ValueError("experiment runtime kind must be non-empty")
    container_digest = str(plan.container_digest or task.image_digest).strip()
    if plan.container_digest and container_digest != task.image_digest:
        raise ValueError("runtime plan container digest differs from TaskSpec")
    network_policy = str(plan.network_policy or task.network_policy).strip()
    if plan.network_policy and network_policy != task.network_policy:
        raise ValueError("runtime plan network policy differs from TaskSpec")

    resource_limits = dict(task.resource_limits)
    for key, value in plan.resource_limits.items():
        if key in resource_limits and resource_limits[key] != value:
            raise ValueError(
                f"runtime plan resource limit {key} differs from TaskSpec"
            )
        resource_limits[key] = value
    _enforce_task_resource_ceiling(
        resource_limits,
        key="max_tokens",
        requested=budget.max_tokens,
    )
    _enforce_task_resource_ceiling(
        resource_limits,
        key="max_cost_usd",
        requested=budget.max_cost_usd,
    )
    _enforce_task_resource_ceiling(
        resource_limits,
        key="timeout_s",
        requested=budget.timeout_s,
    )
    resource_limits.update(
        {
            "max_tokens": int(budget.max_tokens),
            "max_cost_usd": float(budget.max_cost_usd),
            "timeout_s": int(budget.timeout_s),
            "max_retries": int(budget.max_retries),
        }
    )
    compute_payload = {
        "runtime": runtime_kind,
        "runtime_identity": _runtime_identity(runtime),
        "model": plan.model,
        "container_digest": container_digest,
        "architecture": task.architecture,
        "os_name": task.os_name,
        "network_policy": network_policy,
        "resource_limits": resource_limits,
        "environment": dict(plan.environment),
        "runtime_metadata": dict(plan.runtime_metadata),
        "denied_paths": list(plan.denied_paths),
        "inherit_env": bool(plan.inherit_env),
        "tools": list(plan.tools),
        "execution_mode": plan.execution_mode,
    }
    return {
        **compute_payload,
        "compute_resource_hash": _canonical_hash(compute_payload),
        "treatment_hash": plan.treatment.treatment_hash,
        "plan_fingerprint": _plan_fingerprint(runtime, plan),
    }


def _enforce_task_resource_ceiling(
    resource_limits: Mapping[str, Any],
    *,
    key: str,
    requested: int | float,
) -> None:
    if key not in resource_limits:
        return
    try:
        limit = float(resource_limits[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"TaskSpec resource limit {key} must be numeric") from exc
    if not math.isfinite(limit) or limit < 0:
        raise ValueError(f"TaskSpec resource limit {key} is invalid")
    if float(requested) > limit:
        raise ValueError(f"arm budget exceeds TaskSpec resource limit {key}")


def _validate_run_result_provenance(
    result,
    *,
    runtime: AgentRuntime,
    plan: RuntimeArmPlan,
    expected_task_id: str,
    expected_run_id: str,
) -> None:
    runtime_kind = str(getattr(runtime, "kind", "") or "")
    if result.run_id != expected_run_id:
        raise ValueError("runtime result run_id differs from its start handle")
    if result.task_id != expected_task_id:
        raise ValueError("runtime result task_id differs from isolated execution")
    if not str(result.session_id or "").strip():
        raise ValueError("runtime result session_id must be non-empty")
    if result.runtime != runtime_kind:
        raise ValueError("runtime result kind differs from frozen runtime plan")
    if result.resolved_model != plan.model:
        raise ValueError("runtime served model differs from frozen model plan")
    result_hash = str(result.result_hash or "").strip().casefold()
    if (
        len(result_hash) != 64
        or any(
            character not in "0123456789abcdef"
            for character in result_hash
        )
    ):
        raise ValueError("runtime result_hash must be a sha256 digest")
    try:
        cost_usd = float(result.cost_usd)
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime result cost must be numeric") from exc
    if not math.isfinite(cost_usd) or cost_usd < 0:
        raise ValueError("runtime result cost must be finite and non-negative")
    missing = [
        field_name
        for field_name, value in (
            ("model_provenance", result.model_provenance),
            ("cost_provenance", result.cost_provenance),
            ("token_provenance", result.token_provenance),
        )
        if not str(value or "").strip()
    ]
    if "tokens_in" not in result.token_usage or "tokens_out" not in result.token_usage:
        missing.append("token_usage")
    if missing:
        raise ValueError(
            "experiment runtime provenance is incomplete: "
            + ", ".join(missing)
        )
    for key in ("tokens_in", "tokens_out"):
        value = result.token_usage[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ValueError(
                f"runtime result token_usage {key} must be a "
                "non-negative integer"
            )


def _merge_token_usage(
    aggregate: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(aggregate)
    for key, value in observed.items():
        if isinstance(value, bool):
            merged[key] = value
        elif isinstance(value, (int, float)):
            numeric = float(value)
            if (
                not math.isfinite(numeric)
                or numeric < 0
                or not numeric.is_integer()
            ):
                raise ValueError("runtime token usage must be finite and non-negative")
            merged[key] = int(merged.get(key) or 0) + int(numeric)
        else:
            merged[key] = value
    return merged


def _total_tokens(token_usage: Mapping[str, Any]) -> int:
    return int(token_usage.get("tokens_in") or 0) + int(
        token_usage.get("tokens_out") or 0
    )


def _budget_ceiling_failure(
    budget: ArmBudget,
    *,
    total_cost: float,
    total_tokens: int,
    elapsed_s: float,
) -> str:
    if total_tokens > int(budget.max_tokens):
        return "arm exceeded its ex-ante token ceiling"
    if total_cost > float(budget.max_cost_usd):
        return "arm exceeded its ex-ante cost ceiling"
    if elapsed_s > float(budget.timeout_s):
        return "arm exceeded its ex-ante time ceiling"
    return ""


def _retry_budget_available(
    budget: ArmBudget,
    *,
    total_cost: float,
    total_tokens: int,
    elapsed_s: float,
) -> bool:
    return (
        total_tokens < int(budget.max_tokens)
        and total_cost < float(budget.max_cost_usd)
        and elapsed_s < float(budget.timeout_s)
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "RepositoryArmExecutor",
    "RuntimeArmPlan",
]
