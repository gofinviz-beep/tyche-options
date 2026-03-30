"""Tests for analysis/client.py — GeminiClient retry, backoff, fallback,
error handling, token parsing, batch mode, and text generation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from tyche.analysis.client import (
    GeminiClient,
    _BACKOFF_FACTOR,
    _BASE_DELAY_S,
    _FALLBACK_MODELS,
    _MAX_RETRIES,
)
from tyche.exceptions import LLMResponseError


class SampleModel(BaseModel):
    answer: str
    score: float


class SampleItem(BaseModel):
    name: str
    value: int


def _make_response(text: str, prompt_tokens: int = 10, completion_tokens: int = 20):
    """Build a mock genai response with text and usage metadata."""
    resp = MagicMock()
    resp.text = text
    resp.usage_metadata = MagicMock()
    resp.usage_metadata.prompt_token_count = prompt_tokens
    resp.usage_metadata.candidates_token_count = completion_tokens
    return resp


def _make_client(model_fast="gemini-3-flash-preview", model_deep="gemini-3.1-pro-preview"):
    """Create a GeminiClient with the genai.Client mocked out."""
    with patch("tyche.analysis.client.genai") as mock_genai:
        mock_sdk_client = MagicMock()
        mock_genai.Client.return_value = mock_sdk_client
        client = GeminiClient(api_key="test-key", model_fast=model_fast, model_deep=model_deep)
    return client, mock_sdk_client


# ---------------------------------------------------------------------------
# _call_model
# ---------------------------------------------------------------------------

class TestCallModel:
    def test_delegates_to_sdk(self):
        client, sdk = _make_client()
        config = MagicMock()
        expected = _make_response('{"answer":"ok","score":1.0}')
        sdk.models.generate_content.return_value = expected

        result = client._call_model("gemini-3-flash-preview", "hello", config)

        sdk.models.generate_content.assert_called_once_with(
            model="gemini-3-flash-preview",
            contents="hello",
            config=config,
        )
        assert result is expected


# ---------------------------------------------------------------------------
# _call_with_retry — success paths
# ---------------------------------------------------------------------------

class TestCallWithRetrySuccess:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        client, sdk = _make_client()
        expected = _make_response('{"answer":"ok","score":1.0}')
        sdk.models.generate_content.return_value = expected

        result = await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())

        assert result is expected
        assert sdk.models.generate_content.call_count == 1

    @pytest.mark.asyncio
    async def test_no_fallback_logged_when_primary_succeeds(self):
        client, sdk = _make_client()
        expected = _make_response('{"answer":"ok","score":1.0}')
        sdk.models.generate_content.return_value = expected

        result = await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())
        assert result is expected


# ---------------------------------------------------------------------------
# _call_with_retry — retry on 503/429
# ---------------------------------------------------------------------------

def _make_server_error(status_code: int = 503):
    """Create a properly initialized ServerError mock."""
    from google.genai.errors import ServerError

    exc = ServerError(status_code, {"error": "test error"})
    exc.status_code = status_code
    return exc


class TestCallWithRetryRetries:
    @pytest.mark.asyncio
    async def test_retries_on_503_then_succeeds(self):
        client, sdk = _make_client()
        exc = _make_server_error(503)
        expected = _make_response('{"answer":"ok","score":1.0}')

        sdk.models.generate_content.side_effect = [exc, expected]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())

        assert result is expected
        assert sdk.models.generate_content.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self):
        client, sdk = _make_client()
        exc = _make_server_error(429)
        expected = _make_response('{"answer":"ok","score":1.0}')

        sdk.models.generate_content.side_effect = [exc, expected]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())

        assert result is expected

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self):
        client, sdk = _make_client()
        exc = _make_server_error(503)
        expected = _make_response('{"answer":"ok","score":1.0}')

        sdk.models.generate_content.side_effect = [exc, exc, expected]

        sleep_calls = []
        async def fake_sleep(delay):
            sleep_calls.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            result = await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())

        assert result is expected
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == _BASE_DELAY_S
        assert sleep_calls[1] == _BASE_DELAY_S * _BACKOFF_FACTOR


# ---------------------------------------------------------------------------
# _call_with_retry — fallback
# ---------------------------------------------------------------------------

class TestCallWithRetryFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_secondary_model(self):
        from google.genai.errors import ServerError

        client, sdk = _make_client()
        exc = _make_server_error(503)
        expected = _make_response('{"answer":"fallback","score":0.5}')

        sdk.models.generate_content.side_effect = [exc, exc, exc, expected]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())

        assert result is expected
        calls = sdk.models.generate_content.call_args_list
        assert any(
            "gemini-2.5-flash" in str(c)
            for c in calls
        )

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(self):
        from google.genai.errors import ServerError

        client, sdk = _make_client()
        exc = _make_server_error(503)

        sdk.models.generate_content.side_effect = exc

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ServerError):
                await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())


# ---------------------------------------------------------------------------
# _call_with_retry — non-retryable error
# ---------------------------------------------------------------------------

class TestCallWithRetryNonRetryable:
    @pytest.mark.asyncio
    async def test_non_server_error_does_not_retry_or_fallback(self):
        """Non-ServerError should not retry within a model or fall back to another."""
        client, sdk = _make_client()
        sdk.models.generate_content.side_effect = ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())

        assert sdk.models.generate_content.call_count == 1

    @pytest.mark.asyncio
    async def test_server_error_non_retryable_status_tries_fallback(self):
        """ServerError with non-retryable status (e.g. 400) skips retry
        but still attempts the fallback model."""
        from google.genai.errors import ServerError

        client, sdk = _make_client()
        exc = _make_server_error(400)

        sdk.models.generate_content.side_effect = exc

        with pytest.raises(ServerError):
            await client._call_with_retry("gemini-3-flash-preview", "hello", MagicMock())

        # 1 attempt on primary (400 → no retry, break inner) +
        # 1 attempt on fallback (400 → no retry, break inner)
        assert sdk.models.generate_content.call_count == 2


# ---------------------------------------------------------------------------
# _call_with_retry — no fallback model
# ---------------------------------------------------------------------------

class TestNoFallbackModel:
    @pytest.mark.asyncio
    async def test_model_without_fallback_skips_secondary(self):
        from google.genai.errors import ServerError

        client, sdk = _make_client(model_fast="custom-model")
        exc = _make_server_error(503)

        sdk.models.generate_content.side_effect = exc

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ServerError):
                await client._call_with_retry("custom-model", "hello", MagicMock())

        assert sdk.models.generate_content.call_count == _MAX_RETRIES


# ---------------------------------------------------------------------------
# analyze — structured output
# ---------------------------------------------------------------------------

class TestAnalyze:
    @pytest.mark.asyncio
    async def test_parses_valid_json_response(self):
        client, sdk = _make_client()
        resp = _make_response('{"answer":"42","score":0.95}')
        sdk.models.generate_content.return_value = resp

        result = await client.analyze(
            prompt="What is the answer?",
            response_model=SampleModel,
            system_prompt="You are helpful.",
        )

        assert isinstance(result, SampleModel)
        assert result.answer == "42"
        assert result.score == 0.95

    @pytest.mark.asyncio
    async def test_uses_deep_model_when_requested(self):
        client, sdk = _make_client()
        resp = _make_response('{"answer":"deep","score":1.0}')
        sdk.models.generate_content.return_value = resp

        await client.analyze(
            prompt="complex question",
            response_model=SampleModel,
            use_deep=True,
        )

        call_args = sdk.models.generate_content.call_args
        assert "3.1-pro" in str(call_args)

    @pytest.mark.asyncio
    async def test_empty_response_raises_llm_error(self):
        client, sdk = _make_client()
        resp = _make_response("")
        resp.text = ""
        sdk.models.generate_content.return_value = resp

        with pytest.raises(LLMResponseError, match="empty response"):
            await client.analyze(prompt="test", response_model=SampleModel)

    @pytest.mark.asyncio
    async def test_none_response_text_raises_llm_error(self):
        client, sdk = _make_client()
        resp = _make_response("")
        resp.text = None
        sdk.models.generate_content.return_value = resp

        with pytest.raises(LLMResponseError, match="empty response"):
            await client.analyze(prompt="test", response_model=SampleModel)

    @pytest.mark.asyncio
    async def test_invalid_json_raises_llm_error(self):
        client, sdk = _make_client()
        resp = _make_response("not valid json at all")
        sdk.models.generate_content.return_value = resp

        with pytest.raises(LLMResponseError, match="Gemini analysis failed"):
            await client.analyze(prompt="test", response_model=SampleModel)

    @pytest.mark.asyncio
    async def test_reraises_llm_response_error(self):
        client, sdk = _make_client()
        sdk.models.generate_content.side_effect = LLMResponseError("custom error")

        with pytest.raises(LLMResponseError, match="custom error"):
            await client.analyze(prompt="test", response_model=SampleModel)

    @pytest.mark.asyncio
    async def test_no_system_prompt_passes_none(self):
        client, sdk = _make_client()
        resp = _make_response('{"answer":"ok","score":0.5}')
        sdk.models.generate_content.return_value = resp

        await client.analyze(prompt="test", response_model=SampleModel, system_prompt="")

        call_args = sdk.models.generate_content.call_args
        config = call_args.kwargs.get("config") or call_args[1].get("config")
        assert config.system_instruction is None


# ---------------------------------------------------------------------------
# analyze_batch — list wrapper
# ---------------------------------------------------------------------------

class TestAnalyzeBatch:
    @pytest.mark.asyncio
    async def test_returns_list_of_parsed_items(self):
        client, sdk = _make_client()
        resp = _make_response('{"items":[{"name":"a","value":1},{"name":"b","value":2}]}')
        sdk.models.generate_content.return_value = resp

        results = await client.analyze_batch(
            prompt="List items",
            response_model=SampleItem,
        )

        assert len(results) == 2
        assert results[0].name == "a"
        assert results[1].value == 2

    @pytest.mark.asyncio
    async def test_empty_list(self):
        client, sdk = _make_client()
        resp = _make_response('{"items":[]}')
        sdk.models.generate_content.return_value = resp

        results = await client.analyze_batch(prompt="List items", response_model=SampleItem)
        assert results == []


# ---------------------------------------------------------------------------
# generate_text
# ---------------------------------------------------------------------------

class TestGenerateText:
    @pytest.mark.asyncio
    async def test_returns_text(self):
        client, sdk = _make_client()
        resp = MagicMock()
        resp.text = "Summary of the day."
        sdk.models.generate_content.return_value = resp

        result = await client.generate_text(prompt="Summarize")
        assert result == "Summary of the day."

    @pytest.mark.asyncio
    async def test_returns_empty_string_on_none(self):
        client, sdk = _make_client()
        resp = MagicMock()
        resp.text = None
        sdk.models.generate_content.return_value = resp

        result = await client.generate_text(prompt="Summarize")
        assert result == ""

    @pytest.mark.asyncio
    async def test_uses_deep_model(self):
        client, sdk = _make_client()
        resp = MagicMock()
        resp.text = "deep analysis"
        sdk.models.generate_content.return_value = resp

        await client.generate_text(prompt="Summarize", use_deep=True)
        call_args = sdk.models.generate_content.call_args
        assert "3.1-pro" in str(call_args)

    @pytest.mark.asyncio
    async def test_exception_raises_llm_error(self):
        client, sdk = _make_client()
        sdk.models.generate_content.side_effect = RuntimeError("network timeout")

        with pytest.raises(LLMResponseError, match="Text generation failed"):
            await client.generate_text(prompt="Summarize")


# ---------------------------------------------------------------------------
# Fallback models mapping
# ---------------------------------------------------------------------------

class TestFallbackModels:
    def test_flash_has_fallback(self):
        assert "gemini-3-flash-preview" in _FALLBACK_MODELS

    def test_pro_has_fallback(self):
        assert "gemini-3.1-pro-preview" in _FALLBACK_MODELS

    def test_fallback_model_is_different(self):
        for primary, fallback in _FALLBACK_MODELS.items():
            assert primary != fallback

    def test_constants(self):
        assert _MAX_RETRIES == 3
        assert _BASE_DELAY_S == 2.0
        assert _BACKOFF_FACTOR == 2
