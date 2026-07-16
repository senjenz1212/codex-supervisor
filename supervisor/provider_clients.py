"""Concrete provider adapters behind model-neutral client protocols."""
from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any, Mapping, Sequence

from .model_client import (
    CallableModelClient,
    ModelRequest,
    ModelResponse,
)
from .provider_routing import direct_anthropic_env


def _is_async_callable(value: Any) -> bool:
    if inspect.iscoroutinefunction(value):
        return True
    call = getattr(value, "__call__", None)
    return call is not None and inspect.iscoroutinefunction(call)


async def _invoke_provider(
    callable_value: Any,
    *,
    field: str,
    kwargs: Mapping[str, Any],
) -> Any:
    if not callable(callable_value):
        raise TypeError(f"{field} must be callable")
    if _is_async_callable(callable_value):
        result = callable_value(**dict(kwargs))
    else:
        result = await asyncio.to_thread(
            callable_value,
            **dict(kwargs),
        )
    return await result if inspect.isawaitable(result) else result


class AnthropicModelClient(CallableModelClient):
    """Adapt an Anthropic-compatible async Messages client."""

    def __init__(self, client: Any) -> None:
        self.client = client
        super().__init__(self._complete_anthropic)

    async def _complete_anthropic(self, request: ModelRequest) -> ModelResponse:
        system = "\n\n".join(
            message.content
            for message in request.messages
            if message.role == "system"
        )
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role in {"user", "assistant"}
        ]
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": int(request.max_tokens),
            "temperature": float(request.temperature),
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        response = await _invoke_provider(
            self.client.messages.create,
            field="client.messages.create",
            kwargs=kwargs,
        )
        text = "\n".join(
            str(getattr(block, "text", ""))
            for block in (getattr(response, "content", ()) or ())
            if getattr(block, "text", None)
        )
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=text,
            resolved_model=str(getattr(response, "model", None) or ""),
            provider="anthropic",
            usage={
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            },
            raw=response,
        )


class OpenAICompatibleModelClient(CallableModelClient):
    """Adapt sync or async OpenAI-compatible chat-completion clients."""

    def __init__(self, client: Any, *, provider: str = "openai_compatible") -> None:
        self.client = client
        self.provider = provider
        super().__init__(self._complete_openai)

    async def _complete_openai(self, request: ModelRequest) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "max_tokens": int(request.max_tokens),
            "temperature": float(request.temperature),
        }
        if request.tools:
            kwargs["tools"] = [dict(tool) for tool in request.tools]
        response = await _invoke_provider(
            self.client.chat.completions.create,
            field="client.chat.completions.create",
            kwargs=kwargs,
        )
        choices: Sequence[Any] = getattr(response, "choices", ()) or ()
        if not choices:
            raise ValueError("OpenAI-compatible response has no choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, list):
            text = "\n".join(
                str(
                    item.get("text")
                    if isinstance(item, Mapping)
                    else getattr(item, "text", "")
                )
                for item in content
            )
        else:
            text = str(content or "")
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=text,
            resolved_model=str(getattr(response, "model", None) or ""),
            provider=self.provider,
            usage={
                "input_tokens": int(
                    getattr(usage, "prompt_tokens", 0)
                    or getattr(usage, "input_tokens", 0)
                    or 0
                ),
                "output_tokens": int(
                    getattr(usage, "completion_tokens", 0)
                    or getattr(usage, "output_tokens", 0)
                    or 0
                ),
            },
            raw=response,
        )


class OpenAIEmbeddingClient:
    """Narrow embedding seam used by semantic drift detection."""

    def __init__(
        self,
        client: Any,
        *,
        provider: str = "openai_compatible",
        timeout_s: float = 60.0,
    ) -> None:
        if float(timeout_s) <= 0:
            raise ValueError("timeout_s must be positive")
        self.client = client
        self.provider = provider
        self.timeout_s = float(timeout_s)

    async def embed(self, *, model: str, texts: Sequence[str]) -> list[list[float]]:
        response = await asyncio.wait_for(
            _invoke_provider(
                self.client.embeddings.create,
                field="client.embeddings.create",
                kwargs={"model": model, "input": list(texts)},
            ),
            timeout=self.timeout_s,
        )
        rows = getattr(response, "data", ()) or ()
        return [
            [float(value) for value in (getattr(row, "embedding", ()) or ())]
            for row in rows
        ]


class ClaudeAgentSdkModelClient(CallableModelClient):
    """Optional Claude Agent SDK adapter kept outside core decision modules."""

    def __init__(self, *, sdk_loader: Any | None = None) -> None:
        self._sdk_loader = sdk_loader or _load_claude_agent_sdk
        super().__init__(self._complete_sdk)

    async def _complete_sdk(self, request: ModelRequest) -> ModelResponse:
        client_cls, options_cls = self._sdk_loader()
        system = "\n\n".join(
            message.content
            for message in request.messages
            if message.role == "system"
        )
        user_message = "\n\n".join(
            message.content
            for message in request.messages
            if message.role == "user"
        )
        options_kwargs: dict[str, Any] = {
            "system_prompt": system,
            "model": request.model,
            "max_turns": int(request.metadata.get("max_turns") or 2),
            "allowed_tools": [],
            "effort": str(request.metadata.get("effort") or "medium"),
            "env": direct_anthropic_env(
                request.metadata.get("environment")
                if isinstance(request.metadata.get("environment"), Mapping)
                else os.environ
            ),
        }
        permission_mode = str(
            request.metadata.get("permission_mode") or ""
        ).strip()
        if permission_mode:
            options_kwargs["permission_mode"] = permission_mode
        options = options_cls(**options_kwargs)
        outputs: list[str] = []
        resolved_model = ""
        async with client_cls(options=options) as client:
            await client.query(user_message)
            async for message in client.receive_response():
                observed_model = getattr(message, "model", None)
                if observed_model:
                    resolved_model = str(observed_model)
                for block in getattr(message, "content", ()) or ():
                    text = getattr(block, "text", None)
                    if text:
                        outputs.append(str(text))
        return ModelResponse(
            text="\n".join(outputs),
            resolved_model=resolved_model,
            provider="anthropic",
        )


def _load_claude_agent_sdk() -> tuple[Any, Any]:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    return ClaudeSDKClient, ClaudeAgentOptions


__all__ = [
    "AnthropicModelClient",
    "ClaudeAgentSdkModelClient",
    "OpenAICompatibleModelClient",
    "OpenAIEmbeddingClient",
]
