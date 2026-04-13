"""Gemini client wrapper with retry, backoff, and model fallback."""

from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

import structlog
from google import genai
from google.genai.errors import ServerError
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel

from tyche.exceptions import LLMResponseError

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)

_FALLBACK_MODELS: dict[str, str] = {
    "gemini-3-flash-preview": "gemini-2.5-flash",
    "gemini-3.1-pro-preview": "gemini-2.5-flash",
    "gemini-2.5-flash-lite": "gemini-2.0-flash",
}

_MAX_RETRIES = 3
_BASE_DELAY_S = 2.0
_BACKOFF_FACTOR = 2


class GeminiClient:
    """Wraps the google-genai SDK for structured LLM interactions.

    Supports both fast (Flash) and deep (Pro) model tiers with
    automatic retry, exponential backoff, and model fallback on 503.
    """

    def __init__(
        self,
        api_key: str,
        model_fast: str = "gemini-3-flash-preview",
        model_deep: str = "gemini-3.1-pro-preview",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_fast = model_fast
        self._model_deep = model_deep

    def _call_model(
        self,
        model: str,
        contents: str,
        config: GenerateContentConfig,
    ) -> Any:
        """Synchronous wrapper around the genai SDK (it is sync internally)."""
        return self._client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    async def _call_with_retry(
        self,
        model: str,
        contents: str,
        config: GenerateContentConfig,
    ) -> Any:
        """Call the model with exponential backoff and model fallback on 503."""
        models_to_try = [model]
        fallback = _FALLBACK_MODELS.get(model)
        if fallback and fallback != model:
            models_to_try.append(fallback)

        last_exc: Exception | None = None

        for current_model in models_to_try:
            delay = _BASE_DELAY_S
            non_retryable = False
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    response = await asyncio.to_thread(
                        self._call_model, current_model, contents, config
                    )
                    if current_model != model:
                        logger.info(
                            "llm_fallback_succeeded",
                            primary=model,
                            fallback=current_model,
                        )
                    return response
                except ServerError as exc:
                    last_exc = exc
                    status = getattr(exc, "status_code", 0)
                    if status in (503, 429) and attempt < _MAX_RETRIES:
                        logger.warning(
                            "llm_retry",
                            model=current_model,
                            attempt=attempt,
                            delay_s=delay,
                            status=status,
                        )
                        await asyncio.sleep(delay)
                        delay *= _BACKOFF_FACTOR
                        continue
                    logger.warning(
                        "llm_model_exhausted",
                        model=current_model,
                        attempts=attempt,
                        error=str(exc),
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    non_retryable = True
                    break
            if non_retryable:
                break

        raise last_exc or LLMResponseError("All model attempts failed")

    async def analyze(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: str = "",
        use_deep: bool = False,
        temperature: float = 0.3,
        model_override: str | None = None,
    ) -> T:
        """Send a prompt and parse the response into a Pydantic model.

        Args:
            prompt: The user/analysis prompt.
            response_model: Pydantic model class for structured output.
            system_prompt: System-level instructions (grounding rules).
            use_deep: Use the Pro model instead of Flash.
            temperature: Sampling temperature (lower = more deterministic).
            model_override: Explicit model name, bypasses use_deep/fast selection.

        Returns:
            Parsed Pydantic model instance.
        """
        if model_override:
            model = model_override
        else:
            model = self._model_deep if use_deep else self._model_fast

        config = GenerateContentConfig(
            system_instruction=system_prompt if system_prompt else None,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=response_model,
        )

        try:
            response = await self._call_with_retry(model, prompt, config)

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
        model_override: str | None = None,
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
            model_override=model_override,
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
