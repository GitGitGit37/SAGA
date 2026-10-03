"""Thin wrapper around the Anthropic SDK for structured (Pydantic-validated) output.

Everything that talks to Claude goes through StructuredLLM, so tests can swap in a fake.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from app.settings import Settings, get_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
Tier = Literal["fast", "reasoning"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LLMError(RuntimeError):
    pass


class StructuredLLM(Protocol):
    def parse(self, *, tier: Tier, system: str, prompt: str, schema: type[T],
              effort: Effort = "medium", max_tokens: int = 16000) -> T: ...


class ClaudeLLM:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)

    def model_for(self, tier: Tier) -> str:
        return self.settings.claude_fast_model if tier == "fast" else self.settings.claude_reasoning_model

    def parse(self, *, tier: Tier, system: str, prompt: str, schema: type[T],
              effort: Effort = "medium", max_tokens: int = 16000) -> T:
        model = self.model_for(tier)
        try:
            response = self.client.beta.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": effort},
                # Server-side refusal fallback: if a safety classifier declines, the API
                # re-runs the request on a fallback model within the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited by Claude API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"could not reach Claude API: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LLMError(f"Claude declined the request ({getattr(response.stop_details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"response hit max_tokens={max_tokens}")
        if response.parsed_output is None:
            raise LLMError("response did not contain parseable structured output")
        log.info("claude %s: %s in / %s out tokens", model,
                 response.usage.input_tokens, response.usage.output_tokens)
        return response.parsed_output


def get_llm() -> StructuredLLM | None:
    """Claude if an API key is configured, else None (callers fall back to rules)."""
    try:
        return ClaudeLLM()
    except LLMError:
        return None
