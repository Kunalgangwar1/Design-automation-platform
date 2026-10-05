"""Provider-independent LLM contract and application error categories."""

from typing import Protocol

from design_automation.models import LLMRequest, LLMResponse


class LLMClient(Protocol):
    """Small provider contract consumed by the generation service."""

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def generate(self, request: LLMRequest) -> LLMResponse: ...


class GenerationError(RuntimeError):
    """Base application error with an explicit retry policy."""

    retryable = False


class LLMConfigurationError(GenerationError):
    """Provider or credential configuration is invalid."""


class LLMAuthenticationError(GenerationError):
    """The provider rejected authentication or authorization."""


class LLMTimeoutError(GenerationError):
    """The provider request exceeded its timeout."""

    retryable = True


class LLMConnectionError(GenerationError):
    """The provider could not be reached."""

    retryable = True


class LLMRateLimitError(GenerationError):
    """The provider temporarily rate-limited the request."""

    retryable = True


class LLMTemporaryError(GenerationError):
    """The provider reported a retryable server-side failure."""

    retryable = True


class LLMInvalidRequestError(GenerationError):
    """The provider rejected a deterministic request parameter."""


class LLMModelError(GenerationError):
    """The configured provider model is unavailable or invalid."""


class LLMProviderError(GenerationError):
    """An otherwise unclassified provider failure occurred."""
