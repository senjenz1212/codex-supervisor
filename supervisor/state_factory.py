"""Trusted composition root for State and ledger checkpoint dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Mapping

from .config import Config, LedgerCheckpointsCfg
from .evidence_committer import HmacCheckpointAuthority
from .ledger_checkpoints import (
    CheckpointSignatureVerifier,
    FilesystemTrustedCheckpointPinStore,
    LedgerCheckpointCoordinator,
    LedgerCheckpointPolicy,
    LedgerCheckpointStore,
    TrustedCheckpointPinStore,
)
from .evidence_ledger import Signer
from .state import State


class StateFactoryError(RuntimeError):
    """Authoritative state dependencies are absent or not externally trusted."""


DAEMON_REQUIRED_STATE_METHODS = frozenset(
    {
        "ack_decision",
        "active_run_watches",
        "active_runs",
        "answer_ask",
        "claim_next_dual_agent_workflow_job_for_dispatch",
        "complete_action",
        "complete_autoresearch_experiment_run",
        "complete_dual_agent_workflow_job",
        "complete_supervisor_turn",
        "count_autoresearch_experiments_started_since",
        "create_ask",
        "dead_letter_decision",
        "end_run",
        "enqueue_decision",
        "get_ask",
        "get_dual_agent_workflow_job",
        "get_run",
        "get_run_by_session",
        "get_run_snapshot",
        "get_supervisor_conversation",
        "get_tail_offset",
        "ingest_source_line",
        "list_autoresearch_experiment_queue",
        "list_p11_audit_candidate_run_ids",
        "list_quality_trend_rows",
        "mark_autoresearch_experiment_run_started",
        "mark_run_watch_notified",
        "next_decision",
        "query_quality_trends",
        "read_dual_agent_gate_events",
        "read_events_since",
        "recent_events",
        "recent_supervisor_turns",
        "record_action",
        "record_supervisor_notification",
        "record_supervisor_turn",
        "register_run",
        "reserve_dual_agent_workflow_job",
        "retry_decision",
        "set_tail_offset",
        "update_dual_agent_workflow_job",
        "update_quality_trend_audit",
        "upsert_dual_agent_workflow_job",
        "upsert_quality_trend_row",
        "upsert_supervisor_conversation",
        "verify_event_ledger_structure",
        "write_event",
        "write_event_and_tail_offset",
        "write_hook_request",
        "write_verdict",
    }
)


@dataclass(frozen=True)
class CheckpointRuntime:
    """Runtime-owned authority material returned by an operator plugin.

    The provider owns key access and rollback-independent pin persistence.
    Supervisor receives only narrow signing, verification, and pin interfaces.
    """

    signer: Signer
    verifier: CheckpointSignatureVerifier | Callable[
        [bytes, Mapping[str, Any]],
        bool,
    ]
    trusted_pin_store: TrustedCheckpointPinStore
    provider_id: str
    externally_managed: bool
    rollback_independent: bool


CheckpointRuntimeResolver = Callable[
    [str, LedgerCheckpointsCfg],
    CheckpointRuntime,
]


def build_state(
    cfg: Config,
    *,
    checkpoint_runtime_resolver: CheckpointRuntimeResolver | None = None,
) -> Any:
    """Build the configured State without silently weakening assurance."""
    settings = cfg.supervisor.ledger_checkpoints
    if settings.mode == "diagnostic_only":
        return State(cfg.supervisor.state_db)

    resolver = checkpoint_runtime_resolver or _resolve_runtime_provider
    try:
        runtime = resolver(settings.runtime_provider, settings)
    except Exception as exc:
        raise StateFactoryError(
            "authoritative ledger checkpoint runtime could not be resolved"
        ) from exc
    _validate_runtime(runtime)
    coordinator = LedgerCheckpointCoordinator(
        signer=runtime.signer,
        verifier=runtime.verifier,
        checkpoint_store=LedgerCheckpointStore(
            settings.checkpoint_store_path
        ),
        trusted_pin_store=runtime.trusted_pin_store,
        policy=LedgerCheckpointPolicy(
            max_events_between_checkpoints=(
                settings.max_events_between_checkpoints
            ),
        ),
        signer_provider_id=runtime.provider_id,
    )
    return State(
        cfg.supervisor.state_db,
        ledger_checkpoint_coordinator=coordinator,
    )


def require_state_capabilities(
    state: Any,
    *,
    required_methods: frozenset[str] | set[str],
    profile: str,
) -> None:
    """Fail composition before a partial backend reaches live subsystems."""
    missing = sorted(
        name
        for name in required_methods
        if not callable(getattr(state, name, None))
    )
    if missing:
        raise StateFactoryError(
            f"state backend {type(state).__name__} does not support "
            f"{profile}: missing {', '.join(missing)}"
        )


def _resolve_runtime_provider(
    provider_ref: str,
    settings: LedgerCheckpointsCfg,
) -> CheckpointRuntime:
    module_name, separator, attribute_name = provider_ref.partition(":")
    if not separator or not module_name or not attribute_name:
        raise StateFactoryError(
            "runtime_provider must be a module:callable reference"
        )
    provider = getattr(import_module(module_name), attribute_name)
    if not callable(provider):
        raise StateFactoryError("runtime_provider is not callable")
    runtime = provider(settings)
    if not isinstance(runtime, CheckpointRuntime):
        raise StateFactoryError(
            "runtime_provider must return CheckpointRuntime"
        )
    return runtime


def _validate_runtime(runtime: CheckpointRuntime) -> None:
    if not isinstance(runtime, CheckpointRuntime):
        raise StateFactoryError(
            "checkpoint runtime resolver returned an invalid value"
        )
    if (
        type(runtime.provider_id) is not str
        or not runtime.provider_id.strip()
    ):
        raise StateFactoryError("checkpoint runtime provider_id is required")
    if runtime.provider_id != runtime.provider_id.strip():
        raise StateFactoryError(
            "checkpoint runtime provider_id must be canonical"
        )
    if not runtime.externally_managed:
        raise StateFactoryError(
            "authoritative signer/verifier must be externally managed"
        )
    if not runtime.rollback_independent:
        raise StateFactoryError(
            "trusted checkpoint pins must be rollback independent"
        )
    if isinstance(runtime.signer, HmacCheckpointAuthority):
        raise StateFactoryError(
            "hermetic HMAC checkpoint authority is not production authority"
        )
    if isinstance(
        runtime.trusted_pin_store,
        FilesystemTrustedCheckpointPinStore,
    ):
        raise StateFactoryError(
            "filesystem trusted pins are not rollback independent"
        )


__all__ = [
    "CheckpointRuntime",
    "CheckpointRuntimeResolver",
    "DAEMON_REQUIRED_STATE_METHODS",
    "StateFactoryError",
    "build_state",
    "require_state_capabilities",
]
