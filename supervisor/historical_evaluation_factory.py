"""Trusted composition root for historical evaluation operations."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Callable

from .config import Config, HistoricalEvaluationCfg
from .historical_evaluation import (
    EvidenceResolver,
    HistoricalEvaluationService,
    HistoricalExecutor,
    HistoricalState,
)


class HistoricalEvaluationFactoryError(RuntimeError):
    """Historical evaluation is disabled or its runtime plugin is invalid."""


@dataclass(frozen=True)
class HistoricalEvaluationRuntime:
    """Operator-owned, model/task-specific execution dependencies."""

    evidence_resolver: EvidenceResolver
    rerun_executor: HistoricalExecutor
    regrade_executor: HistoricalExecutor
    replay_executor: HistoricalExecutor
    provider_id: str


HistoricalEvaluationRuntimeResolver = Callable[
    [str, HistoricalEvaluationCfg, HistoricalState],
    HistoricalEvaluationRuntime,
]


def maybe_build_historical_evaluation_service(
    cfg: Config,
    state: HistoricalState,
    *,
    runtime_resolver: HistoricalEvaluationRuntimeResolver | None = None,
) -> HistoricalEvaluationService | None:
    settings = cfg.supervisor.historical_evaluation
    if not settings.enabled:
        return None
    return build_historical_evaluation_service(
        cfg,
        state,
        runtime_resolver=runtime_resolver,
    )


def build_historical_evaluation_service(
    cfg: Config,
    state: HistoricalState,
    *,
    runtime_resolver: HistoricalEvaluationRuntimeResolver | None = None,
) -> HistoricalEvaluationService:
    settings = cfg.supervisor.historical_evaluation
    if not settings.enabled:
        raise HistoricalEvaluationFactoryError(
            "historical evaluation is disabled"
        )
    resolver = runtime_resolver or _resolve_runtime_provider
    try:
        runtime = resolver(settings.runtime_provider, settings, state)
    except Exception as exc:
        raise HistoricalEvaluationFactoryError(
            "historical evaluation runtime could not be resolved"
        ) from exc
    _validate_runtime(runtime)
    return HistoricalEvaluationService(
        state=state,
        evidence_resolver=runtime.evidence_resolver,
        rerun_executor=runtime.rerun_executor,
        regrade_executor=runtime.regrade_executor,
        replay_executor=runtime.replay_executor,
        claim_stale_after_s=settings.claim_stale_after_s,
    )


def _resolve_runtime_provider(
    provider_ref: str,
    settings: HistoricalEvaluationCfg,
    state: HistoricalState,
) -> HistoricalEvaluationRuntime:
    module_name, separator, attribute_name = provider_ref.partition(":")
    if not separator or not module_name or not attribute_name:
        raise HistoricalEvaluationFactoryError(
            "runtime_provider must be a module:callable reference"
        )
    provider = getattr(import_module(module_name), attribute_name)
    if not callable(provider):
        raise HistoricalEvaluationFactoryError(
            "runtime_provider is not callable"
        )
    runtime = provider(settings, state)
    if not isinstance(runtime, HistoricalEvaluationRuntime):
        raise HistoricalEvaluationFactoryError(
            "runtime_provider must return HistoricalEvaluationRuntime"
        )
    return runtime


def _validate_runtime(runtime: HistoricalEvaluationRuntime) -> None:
    if not isinstance(runtime, HistoricalEvaluationRuntime):
        raise HistoricalEvaluationFactoryError(
            "historical runtime resolver returned an invalid value"
        )
    if (
        type(runtime.provider_id) is not str
        or not runtime.provider_id
        or runtime.provider_id != runtime.provider_id.strip()
    ):
        raise HistoricalEvaluationFactoryError(
            "historical runtime provider_id must be canonical"
        )
    for field_name in (
        "evidence_resolver",
        "rerun_executor",
        "regrade_executor",
        "replay_executor",
    ):
        if not callable(getattr(runtime, field_name)):
            raise HistoricalEvaluationFactoryError(
                f"historical runtime {field_name} must be callable"
            )


__all__ = [
    "HistoricalEvaluationFactoryError",
    "HistoricalEvaluationRuntime",
    "HistoricalEvaluationRuntimeResolver",
    "build_historical_evaluation_service",
    "maybe_build_historical_evaluation_service",
]
