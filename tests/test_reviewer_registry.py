from __future__ import annotations

from typing import Any

import pytest

from supervisor.agent_runtime import AgentTask
from supervisor.model_client import ModelRequest, ModelResponse
from supervisor.reviewer_registry import (
    CursorCompatibleReviewer,
    ReviewerSpec,
    RuntimeReviewerAdapter,
    StructuredReviewerAdapter,
    configured_reviewers,
)
from supervisor.runtime_execution import RuntimeExecution


class _UnusedModelClient:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("construction must not call the model client")

    async def structured_complete(
        self,
        request: ModelRequest,
        schema: type[Any],
    ) -> Any:
        raise AssertionError("construction must not call the model client")


def _unused_runtime_runner(task: AgentTask) -> RuntimeExecution:
    raise AssertionError("construction must not call the runtime runner")


def test_operational_reviewers_require_provider_neutral_injections() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "operational reviewer execution requires injected "
            "reviewer_adapters or both RuntimeTaskRunner and ModelClient"
        ),
    ):
        configured_reviewers(
            reviewer_output_mode="cursor_sdk",
            reviewer_model=None,
            execution_mode="operational",
        )


def test_operational_reviewers_construct_only_neutral_adapters() -> None:
    reviewers = configured_reviewers(
        reviewer_output_mode="cursor_sdk",
        reviewer_model="provider-neutral-review-model",
        codex_model="provider-neutral-agent-model",
        runtime_runner=_unused_runtime_runner,
        model_client=_UnusedModelClient(),
        litellm_model="provider-neutral-third-model",
        execution_mode="operational",
    )

    assert [type(reviewer) for reviewer in reviewers] == [
        StructuredReviewerAdapter,
        RuntimeReviewerAdapter,
        StructuredReviewerAdapter,
    ]
    assert not any(
        isinstance(reviewer, CursorCompatibleReviewer)
        for reviewer in reviewers
    )
    assert reviewers[0].spec.runtime == "model_client_structured"
    assert reviewers[1].spec.runtime == "codex"
    assert reviewers[2].spec.runtime == "model_client_structured"


def test_operational_reviewers_reject_injected_known_legacy_adapter() -> None:
    legacy = CursorCompatibleReviewer(
        spec=ReviewerSpec(
            reviewer_id="legacy-cursor",
            runtime="cursor_sdk",
            model="legacy-model",
        )
    )

    with pytest.raises(
        ValueError,
        match="operational reviewer execution rejects legacy provider adapters",
    ):
        configured_reviewers(
            reviewer_output_mode="cursor_sdk",
            reviewer_model=None,
            reviewer_adapters=(legacy,),
            execution_mode="operational",
        )


def test_explicit_legacy_reviewer_mode_preserves_compatibility_roster() -> None:
    reviewers = configured_reviewers(
        reviewer_output_mode="cursor_sdk",
        reviewer_model=None,
        execution_mode="legacy",
    )

    assert isinstance(reviewers[0], CursorCompatibleReviewer)
    assert reviewers[1].spec.runtime == "codex_cli"


def test_reviewer_execution_mode_rejects_ambiguous_values() -> None:
    with pytest.raises(ValueError, match="unsupported reviewer execution_mode"):
        configured_reviewers(
            reviewer_output_mode="cursor_sdk",
            reviewer_model=None,
            execution_mode="automatic",
        )
