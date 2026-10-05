"""Unit tests for the delivery-independent generation application facade."""

from datetime import UTC, datetime

import pytest

from design_automation.generation import (
    GenerationApplicationService,
    GenerationNotFoundError,
)
from design_automation.models import (
    GenerationRequest,
    GenerationRequestStatus,
    GenerationResult,
    GenerationRun,
    GenerationRunStatus,
    GroundedContext,
    PersistedGeneration,
    PreparedGenerationRequest,
    RenderedPrompt,
    ValidationResult,
)


def _prepared() -> PreparedGenerationRequest:
    return PreparedGenerationRequest(
        request_id="request-1",
        requested_section="impact_analysis",
        prompt_name="impact_analysis",
        prompt_version=1,
        context=GroundedContext("Partial refunds", "impact_analysis"),
        rendered_prompt=RenderedPrompt(
            "System",
            "Task",
            "impact_analysis",
            1,
        ),
        detected_entities=[],
        status=GenerationRequestStatus.CONTEXT_READY,
    )


def _result() -> GenerationResult:
    return GenerationResult(
        generation_request_id="request-1",
        generation_run_id="run-1",
        requested_section="impact_analysis",
        provider="openai",
        model="test-model",
        generated_text="Grounded impact analysis",
        prompt_name="impact_analysis",
        prompt_version=1,
        latency_ms=10,
        input_tokens=None,
        output_tokens=None,
        provider_request_id=None,
        validation=ValidationResult(valid=True),
        status=GenerationRequestStatus.GENERATED,
    )


class StubPreparer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, int]] = []

    def prepare_generation_context(
        self,
        requirement: str,
        requested_section: str,
        top_k: int,
        max_hops: int,
    ) -> PreparedGenerationRequest:
        self.calls.append((requirement, requested_section, top_k, max_hops))
        return _prepared()


class StubGenerator:
    def __init__(self) -> None:
        self.prepared: list[PreparedGenerationRequest] = []

    def generate(self, prepared: PreparedGenerationRequest) -> GenerationResult:
        self.prepared.append(prepared)
        return _result()


class StubRequestReader:
    def __init__(self, request: GenerationRequest | None) -> None:
        self.request = request

    def get(self, _: str) -> GenerationRequest | None:
        return self.request


class StubRunReader:
    def __init__(self, run: GenerationRun | None) -> None:
        self.run = run

    def latest_for_request(self, _: str) -> GenerationRun | None:
        return self.run


def _stored_models() -> tuple[GenerationRequest, GenerationRun]:
    now = datetime.now(UTC)
    request = GenerationRequest(
        "request-1",
        "Partial refunds",
        "impact_analysis",
        1,
        GenerationRequestStatus.GENERATED,
        now,
        now,
    )
    run = GenerationRun(
        "run-1",
        request.id,
        "openai",
        "test-model",
        "impact_analysis",
        1,
        "Grounded impact analysis",
        10,
        None,
        None,
        None,
        GenerationRunStatus.GENERATED,
        None,
        now,
    )
    return request, run


def test_application_service_coordinates_preparation_then_generation() -> None:
    preparer = StubPreparer()
    generator = StubGenerator()
    service = GenerationApplicationService(
        preparer,
        generator,
        StubRequestReader(None),
        StubRunReader(None),
    )

    result = service.generate("Partial refunds", "impact_analysis", graph_hops=2)

    assert result == _result()
    assert preparer.calls == [("Partial refunds", "impact_analysis", 5, 2)]
    assert generator.prepared == [_prepared()]


def test_application_service_reads_request_and_latest_run() -> None:
    request, run = _stored_models()
    service = GenerationApplicationService(
        StubPreparer(),
        StubGenerator(),
        StubRequestReader(request),
        StubRunReader(run),
    )

    stored = service.get_generation(request.id)

    assert stored == PersistedGeneration(request, run)


def test_application_service_reports_missing_request() -> None:
    service = GenerationApplicationService(
        StubPreparer(),
        StubGenerator(),
        StubRequestReader(None),
        StubRunReader(None),
    )

    with pytest.raises(GenerationNotFoundError, match="missing"):
        service.get_generation("missing")
