"""OpenAI Responses API adapter behind the generic LLM client contract."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)

from design_automation.core import (
    OPENAI_API_KEY,
    OPENAI_API_TIMEOUT_SECONDS,
    OPENAI_MAX_OUTPUT_TOKENS,
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
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
class OpenAIConfig:
    """OpenAI-only settings; repr is disabled to avoid exposing credentials."""

    api_key: str | None = OPENAI_API_KEY
    model: str = OPENAI_MODEL
    timeout_seconds: float = OPENAI_API_TIMEOUT_SECONDS
    max_output_tokens: int = OPENAI_MAX_OUTPUT_TOKENS
    reasoning_effort: str = OPENAI_REASONING_EFFORT

    def validate(self) -> None:
        if not self.api_key:
            raise LLMConfigurationError("OPENAI_API_KEY must be configured")
        if not self.model.strip():
            raise LLMConfigurationError("OPENAI_MODEL cannot be empty")
        if self.timeout_seconds <= 0:
            raise LLMConfigurationError(
                "OPENAI_API_TIMEOUT_SECONDS must be positive"
            )
        if self.max_output_tokens <= 0:
            raise LLMConfigurationError(
                "OPENAI_MAX_OUTPUT_TOKENS must be positive"
            )
        if self.reasoning_effort not in {
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise LLMConfigurationError(
                "OPENAI_REASONING_EFFORT is not supported by gpt-5.6-terra"
            )


class OpenAIClient:
    """Generate text with OpenAI while exposing only application models."""

    def __init__(
        self,
        config: OpenAIConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self._config = config or OpenAIConfig()
        self._client = client

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._config.model

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Call Responses once; bounded retry policy belongs to the service."""

        self._config.validate()
        client = self._client_instance()
        started = perf_counter()
        try:
            response = client.responses.create(
                model=self._config.model,
                instructions=request.system_prompt,
                input=request.user_prompt,
                max_output_tokens=self._config.max_output_tokens,
                reasoning={"effort": self._config.reasoning_effort},
                store=False,
            )
        except OpenAIError as exc:
            raise _map_openai_error(exc) from exc

        usage = getattr(response, "usage", None)
        return LLMResponse(
            provider=self.provider,
            model=str(getattr(response, "model", None) or self.model),
            text=str(getattr(response, "output_text", "") or ""),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            input_tokens=_optional_int(usage, "input_tokens"),
            output_tokens=_optional_int(usage, "output_tokens"),
            provider_request_id=_optional_str(response, "id"),
        )

    def _client_instance(self) -> Any:
        if self._client is None:
            # SDK retries are disabled so the application's small retry budget is
            # the single visible source of retry and API-cost behavior.
            self._client = OpenAI(
                api_key=self._config.api_key,
                timeout=self._config.timeout_seconds,
                max_retries=0,
            )
        return self._client


def _optional_int(value: object, attribute: str) -> int | None:
    raw = getattr(value, attribute, None) if value is not None else None
    return int(raw) if raw is not None else None


def _optional_str(value: object, attribute: str) -> str | None:
    raw = getattr(value, attribute, None)
    return str(raw) if raw is not None else None


def _map_openai_error(error: OpenAIError) -> GenerationError:
    message = str(error) or error.__class__.__name__
    if isinstance(error, (AuthenticationError, PermissionDeniedError)):
        return LLMAuthenticationError(message)
    if isinstance(error, APITimeoutError):
        return LLMTimeoutError(message)
    if isinstance(error, RateLimitError):
        return LLMRateLimitError(message)
    if isinstance(error, APIConnectionError):
        return LLMConnectionError(message)
    if isinstance(error, NotFoundError):
        return LLMModelError(message)
    if isinstance(error, BadRequestError):
        return LLMInvalidRequestError(message)
    if isinstance(error, APIStatusError):
        if error.status_code >= 500:
            return LLMTemporaryError(message)
        return LLMInvalidRequestError(message)
    return LLMProviderError(message)
