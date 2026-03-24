"""Gemini client wrapper with retry and structured output support."""

from __future__ import annotations

import json
from typing import Any, TypeVar

import structlog
from google import genai
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel

from tyche.exceptions import LLMResponseError

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


class GeminiClient:
    """Wraps the google-genai SDK for structured LLM interactions.

    Supports both fast (Flash) and deep (Pro) model tiers.
    All outputs are parsed into Pydantic models for type safety.
    """

    def __init__(
        self,
        api_key: str,
        model_fast: str = "gemini-2.5-flash",
        model_deep: str = "gemini-2.5-pro",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_fast = model_fast
        self._model_deep = model_deep

    async def analyze(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: str = "",
        use_deep: bool = False,
        temperature: float = 0.3,
    ) -> T:
        """Send a prompt and parse the response into a Pydantic model.

        Args:
            prompt: The user/analysis prompt.
            response_model: Pydantic model class for structured output.
            system_prompt: System-level instructions (grounding rules).
            use_deep: Use the Pro model instead of Flash.
            temperature: Sampling temperature (lower = more deterministic).

        Returns:
            Parsed Pydantic model instance.
        """
        model = self._model_deep if use_deep else self._model_fast

        config = GenerateContentConfig(
            system_instruction=system_prompt if system_prompt else None,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=response_model,
        )

        try:
            response = self._client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )

            if not response.text:
                raise LLMResponseError("Gemini returned empty response")

            parsed = response_model.model_validate_json(response.text)

            logger.info(
                "llm_analysis_complete",
                model=model,
                output_type=response_model.__name__,
                prompt_tokens=getattr(
                    response.usage_metadata, "prompt_token_count", None
                ),
                completion_tokens=getattr(
                    response.usage_metadata, "candidates_token_count", None
                ),
            )
            return parsed

        except LLMResponseError:
            raise
        except Exception as exc:
            logger.error(
                "llm_analysis_failed",
                model=model,
                error=str(exc),
                exc_info=True,
            )
            raise LLMResponseError(f"Gemini analysis failed: {exc}") from exc

    async def analyze_batch(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: str = "",
        use_deep: bool = False,
        temperature: float = 0.3,
    ) -> list[T]:
        """Analyze and return a list of structured outputs.

        Wraps the response in a list model automatically.
        """

        class ListWrapper(BaseModel):
            items: list[response_model]  # type: ignore[valid-type]

        wrapper = await self.analyze(
            prompt=prompt,
            response_model=ListWrapper,
            system_prompt=system_prompt,
            use_deep=use_deep,
            temperature=temperature,
        )
        return wrapper.items

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        use_deep: bool = False,
        temperature: float = 0.5,
    ) -> str:
        """Generate free-form text (for journal summaries, explanations)."""
        model = self._model_deep if use_deep else self._model_fast

        config = GenerateContentConfig(
            system_instruction=system_prompt if system_prompt else None,
            temperature=temperature,
        )

        try:
            response = self._client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            return response.text or ""
        except Exception as exc:
            logger.error("llm_text_gen_failed", error=str(exc))
            raise LLMResponseError(f"Text generation failed: {exc}") from exc
