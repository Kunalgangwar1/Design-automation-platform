"""Application facade combining existing preparation and generation services."""

from typing import Protocol

from design_automation.core import DEFAULT_TOP_K
from design_automation.models import (
    GenerationRequest,
    GenerationResult,
    GenerationRun,
    PersistedGeneration,
    PreparedGenerationRequest,
)


class ContextPreparer(Protocol):
    def prepare_generation_context(
        self,
        requirement: str,
        requested_section: str,
        top_k: int,
        max_hops: int,
    ) -> PreparedGenerationRequest: ...


class PreparedGenerator(Protocol):
    def generate(
        self,
        prepared: PreparedGenerationRequest,
    ) -> GenerationResult: ...


class GenerationRequestReader(Protocol):
    def get(self, request_id: str) -> GenerationRequest | None: ...


class GenerationRunReader(Protocol):
    def latest_for_request(self, request_id: str) -> GenerationRun | None: ...


class GenerationNotFoundError(LookupError):
    """Raised when a persisted generation request does not exist."""


class GenerationApplicationService:
    """Thin use-case facade consumed by delivery layers such as FastAPI."""

    def __init__(
        self,
        context_preparer: ContextPreparer,
        generator: PreparedGenerator,
        request_reader: GenerationRequestReader,
        run_reader: GenerationRunReader,
    ) -> None:
        self._context_preparer = context_preparer
        self._generator = generator
        self._request_reader = request_reader
        self._run_reader = run_reader

    def generate(
        self,
        requirement: str,
        requested_section: str,
        graph_hops: int,
        top_k: int = DEFAULT_TOP_K,
    ) -> GenerationResult:
        prepared = self._context_preparer.prepare_generation_context(
            requirement,
            requested_section,
            top_k,
            graph_hops,
        )
        return self._generator.generate(prepared)

    def get_generation(self, request_id: str) -> PersistedGeneration:
        request = self._request_reader.get(request_id)
        if request is None:
            raise GenerationNotFoundError(request_id)
        return PersistedGeneration(
            request=request,
            latest_run=self._run_reader.latest_for_request(request_id),
        )
