from __future__ import annotations

from types import SimpleNamespace

import pytest

from supervisor.model_client import ModelMessage, ModelRequest
from supervisor.provider_clients import (
    AnthropicModelClient,
    ClaudeAgentSdkModelClient,
    OpenAICompatibleModelClient,
    OpenAIEmbeddingClient,
)


@pytest.mark.asyncio
async def test_anthropic_adapter_records_served_model_and_usage():
    seen = {}

    class Messages:
        async def create(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(
                model="claude-served-v1",
                content=[SimpleNamespace(text='{"ok":true}')],
                usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            )

    client = AnthropicModelClient(SimpleNamespace(messages=Messages()))
    response = await client.complete(
        ModelRequest(
            model="claude-requested",
            messages=(
                ModelMessage("system", "system"),
                ModelMessage("user", "hello"),
            ),
            max_tokens=100,
        )
    )

    assert seen["system"] == "system"
    assert seen["messages"] == [{"role": "user", "content": "hello"}]
    assert response.resolved_model == "claude-served-v1"
    assert response.provider == "anthropic"
    assert response.usage == {"input_tokens": 11, "output_tokens": 7}


@pytest.mark.asyncio
async def test_openai_compatible_adapter_supports_async_clients():
    seen = {}

    class Completions:
        async def create(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(
                model="served-model",
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="done"))
                ],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
            )

    raw_client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    response = await OpenAICompatibleModelClient(
        raw_client,
        provider="litellm",
    ).complete(
        ModelRequest(
            model="requested-model",
            messages=(ModelMessage("user", "hello"),),
        )
    )

    assert seen["model"] == "requested-model"
    assert response.text == "done"
    assert response.resolved_model == "served-model"
    assert response.provider == "litellm"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_kind", ["anthropic", "openai"])
async def test_model_adapters_fail_closed_when_provider_omits_served_model(
    provider_kind: str,
) -> None:
    if provider_kind == "anthropic":
        class Messages:
            async def create(self, **_kwargs):
                return SimpleNamespace(
                    model=None,
                    content=[SimpleNamespace(text="done")],
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                )

        client = AnthropicModelClient(
            SimpleNamespace(messages=Messages())
        )
    else:
        class Completions:
            async def create(self, **_kwargs):
                return SimpleNamespace(
                    model=None,
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="done")
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=1,
                        completion_tokens=1,
                    ),
                )

        client = OpenAICompatibleModelClient(
            SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
        )

    with pytest.raises(ValueError, match="resolved_model"):
        await client.complete(
            ModelRequest(
                model="requested-alias",
                messages=(ModelMessage("user", "hello"),),
            )
        )


@pytest.mark.asyncio
async def test_claude_sdk_model_adapter_does_not_report_requested_alias_as_served(
) -> None:
    class Options:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Client:
        def __init__(self, *, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def query(self, _message):
            return None

        async def receive_response(self):
            yield SimpleNamespace(
                model=None,
                content=[SimpleNamespace(text="done")],
            )

    client = ClaudeAgentSdkModelClient(
        sdk_loader=lambda: (Client, Options)
    )

    with pytest.raises(ValueError, match="resolved_model"):
        await client.complete(
            ModelRequest(
                model="claude-alias",
                messages=(ModelMessage("user", "hello"),),
            )
        )


@pytest.mark.asyncio
async def test_embedding_adapter_returns_normalized_vectors():
    class Embeddings:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                data=[
                    SimpleNamespace(embedding=[1, 2.5]),
                    SimpleNamespace(embedding=[3, 4]),
                ]
            )

    client = OpenAIEmbeddingClient(
        SimpleNamespace(embeddings=Embeddings())
    )

    assert await client.embed(model="embed-v1", texts=["a", "b"]) == [
        [1.0, 2.5],
        [3.0, 4.0],
    ]
