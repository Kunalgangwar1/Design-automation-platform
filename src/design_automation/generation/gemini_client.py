"""Google Gemini adapter behind the generic LLM client contract."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from design_automation.core import (
    GEMINI_API_KEY,
    GEMINI_API_TIMEOUT_SECONDS,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL,
)
from design_automation.generation.llm import (
    GenerationError,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMInvalidRequestError,
    LLMModelError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTemporaryError,
    LLMTimeoutError,
)
from design_automation.models import LLMRequest, LLMResponse


@dataclass(frozen=True, repr=False)
class GeminiConfig:
    """Gemini-only settings; repr is disabled to avoid exposing credentials."""

    api_key: str | None = GEMINI_API_KEY
    model: str = GEMINI_MODEL
    timeout_seconds: float = GEMINI_API_TIMEOUT_SECONDS
    max_output_tokens: int = GEMINI_MAX_OUTPUT_TOKENS

    def validate(self) -> None:
        if not self.api_key:
            raise LLMConfigurationError("GEMINI_API_KEY must be configured")
        if not self.model.strip():
            raise LLMConfigurationError("GEMINI_MODEL cannot be empty")
        if self.timeout_seconds <= 0:
            raise LLMConfigurationError(
                "GEMINI_API_TIMEOUT_SECONDS must be positive"
            )
        if self.max_output_tokens <= 0:
            raise LLMConfigurationError(
                "GEMINI_MAX_OUTPUT_TOKENS must be positive"
            )


class GeminiClient:
    """Generate text with Gemini while exposing only application models."""

    def __init__(
        self,
        config: GeminiConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self._config = config or GeminiConfig()
        self._client = client

    @property
    def provider(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._config.model

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Call Gemini once; bounded retry policy belongs to the service."""

        self._config.validate()
        client = self._client_instance()
        started = perf_counter()
        try:
            response = client.models.generate_content(
                model=self._config.model,
                contents=request.user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=request.system_prompt,
                    max_output_tokens=self._config.max_output_tokens,
                ),
            )
        except errors.APIError as exc:
            raise _map_gemini_error(exc) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(str(exc) or "Gemini request timed out") from exc
        except httpx.TransportError as exc:
            raise LLMConnectionError(
                str(exc) or "Could not connect to Gemini"
            ) from exc

        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            provider=self.provider,
            model=str(getattr(response, "model_version", None) or self.model),
            text=str(getattr(response, "text", "") or ""),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            input_tokens=_optional_int(usage, "prompt_token_count"),
            output_tokens=_optional_int(usage, "candidates_token_count"),
            provider_request_id=_optional_str(response, "response_id"),
        )

    def _client_instance(self) -> Any:
        if self._client is None:
            self._client = genai.Client(
                api_key=self._config.api_key,
                http_options=types.HttpOptions(
                    timeout=round(self._config.timeout_seconds * 1000),
                    # Keep the application's retry budget as the one visible
                    # source of retry and provider-fallback behavior.
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client


def _optional_int(value: object, attribute: str) -> int | None:
    raw = getattr(value, attribute, None) if value is not None else None
    return int(raw) if raw is not None else None


def _optional_str(value: object, attribute: str) -> str | None:
    raw = getattr(value, attribute, None)
    return str(raw) if raw is not None else None


def _map_gemini_error(error: errors.APIError) -> GenerationError:
    message = error.message or str(error) or error.__class__.__name__
    if error.code in {401, 403}:
        return LLMAuthenticationError(message)
    if error.code in {408, 504}:
        return LLMTimeoutError(message)
    if error.code == 429:
        return LLMRateLimitError(message)
    if error.code == 404:
        return LLMModelError(message)
    if error.code >= 500:
        return LLMTemporaryError(message)
    if error.code in {400, 409, 422}:
        return LLMInvalidRequestError(message)
    return LLMProviderError(message)
