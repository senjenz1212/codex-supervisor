"""Provider-neutral seam for bounded model completions."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    Protocol,
    Sequence,
    TypeVar,
    runtime_checkable,
)

from pydantic import TypeAdapter, ValidationError


T = TypeVar("T")


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelRequest:
    model: str
    messages: tuple[ModelMessage, ...]
    max_tokens: int = 4096
    temperature: float = 0.0
    tools: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    text: str
    resolved_model: str
    provider: str
    usage: Mapping[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "resolved_model": self.resolved_model,
            "provider": self.provider,
            "usage": dict(self.usage),
            "cost_usd": self.cost_usd,
        }


class StructuredModelResponseError(ValueError):
    """Raised when a structured model response cannot be parsed or validated."""


@runtime_checkable
class ModelClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        ...

    async def structured_complete(
        self,
        request: ModelRequest,
        schema: type[T],
    ) -> T:
        ...


@runtime_checkable
class EmbeddingClient(Protocol):
    async def embed(
        self,
        *,
        model: str,
        texts: Sequence[str],
    ) -> list[list[float]]:
        ...


class CallableModelClient:
    """Adapter for any async provider function returning ``ModelResponse``."""

    def __init__(
        self,
        complete_fn: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> None:
        self._complete_fn = complete_fn

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response = await self._complete_fn(request)
        if not response.resolved_model.strip():
            raise ValueError("model client must record a resolved_model")
        if not response.provider.strip():
            raise ValueError("model client must record a provider")
        return response

    async def structured_complete(
        self,
        request: ModelRequest,
        schema: type[T],
    ) -> T:
        response = await self.complete(request)
        return parse_structured_response(response.text, schema)


def parse_structured_response(text: str, schema: type[T]) -> T:
    """Validate structured output without discarding response provenance."""
    try:
        payload = _parse_json_object(text)
        return TypeAdapter(schema).validate_python(payload)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        raise StructuredModelResponseError(
            "model response did not satisfy the requested schema"
        ) from exc


def _parse_json_object(text: str) -> Any:
    stripped = text.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(?P<payload>.*?)\s*```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        stripped = fenced.group("payload").strip()
    return json.loads(stripped)
