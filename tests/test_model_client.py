from __future__ import annotations

from pydantic import BaseModel
import pytest

from supervisor.model_client import (
    CallableModelClient,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StructuredModelResponseError,
)


class Decision(BaseModel):
    accepted: bool
    reason: str


@pytest.mark.asyncio
async def test_callable_model_client_exposes_one_provider_neutral_response_shape() -> None:
    seen: list[ModelRequest] = []

    async def complete(request: ModelRequest) -> ModelResponse:
        seen.append(request)
        return ModelResponse(
            text="ok",
            resolved_model="provider/model-v2",
            provider="test-provider",
            usage={"input_tokens": 3, "output_tokens": 1},
        )

    client = CallableModelClient(complete)
    response = await client.complete(
        ModelRequest(
            model="model-alias",
            messages=(ModelMessage(role="user", content="hello"),),
        )
    )

    assert seen[0].model == "model-alias"
    assert response.resolved_model == "provider/model-v2"
    assert response.to_dict()["usage"]["input_tokens"] == 3


@pytest.mark.asyncio
async def test_structured_complete_validates_the_model_output() -> None:
    async def complete(_: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text='```json\n{"accepted": true, "reason": "verified"}\n```',
            resolved_model="model-v2",
            provider="test",
        )

    decision = await CallableModelClient(complete).structured_complete(
        ModelRequest(
            model="alias",
            messages=(ModelMessage(role="user", content="judge"),),
        ),
        Decision,
    )

    assert decision == Decision(accepted=True, reason="verified")


@pytest.mark.asyncio
async def test_structured_complete_fails_closed_on_invalid_output() -> None:
    async def complete(_: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text="not json",
            resolved_model="model-v2",
            provider="test",
        )

    with pytest.raises(StructuredModelResponseError):
        await CallableModelClient(complete).structured_complete(
            ModelRequest(
                model="alias",
                messages=(ModelMessage(role="user", content="judge"),),
            ),
            Decision,
        )

