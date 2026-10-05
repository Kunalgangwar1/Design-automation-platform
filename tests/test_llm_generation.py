"""Generation tests use fakes and never call hosted model APIs."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as gemini_errors
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
)

from design_automation.generation import (
    GenerationService,
    GeminiClient,
    GeminiConfig,
    LLMAuthenticationError,
    LLMConnectionError,
    LLMInvalidRequestError,
    LLMModelError,
    LLMRateLimitError,
    LLMTemporaryError,
    LLMTimeoutError,
    OpenAIClient,
    OpenAIConfig,
    build_llm_clients,
    validate_generation,
)
from design_automation.models import (
    DocumentEvidence,
    GenerationRequest,
    GenerationRequestStatus,
    GenerationRun,
    GenerationRunStatus,
    GroundedContext,
    LLMRequest,
    LLMResponse,
    PreparedGenerationRequest,
    RenderedPrompt,
)


def _prepared() -> PreparedGenerationRequest:
    context = GroundedContext(
        requirement="Enable partial refunds",
        requested_section="impact_analysis",
        document_evidence=[
            DocumentEvidence(
                chunk_id="chunk-1",
                document_id="doc-1",
                document_title="Partial refund requirement",
                source_path="requirements/partial_refunds.md",
                section="Acceptance criteria",
                text="Order Service validates partial refunds.",
                retrieval_rank=1,
            )
        ],
        source_provenance=["requirements/partial_refunds.md"],
    )
    return PreparedGenerationRequest(
        request_id="request-1",
        requested_section="impact_analysis",
        prompt_name="impact_analysis",
        prompt_version=1,
        context=context,
        rendered_prompt=RenderedPrompt(
            system_prompt="Use only supplied evidence.",
            task_prompt="Prepare the impact analysis from supplied evidence.",
            prompt_name="impact_analysis",
            prompt_version=1,
            provenance_summary=["requirements/partial_refunds.md"],
        ),
        detected_entities=["Order Service"],
        status=GenerationRequestStatus.CONTEXT_READY,
    )


def _valid_response() -> LLMResponse:
    return LLMResponse(
        provider="openai",
        model="gpt-5.6-terra",
        text=(
            "## Impact Analysis\n\n"
            "The documented requirement affects refund orchestration in the "
            "Order Service. This statement is grounded in "
            "requirements/partial_refunds.md. The supplied graph relationship "
            "should be treated as possible impact evidence, not proof that every "
            "connected component requires modification."
        ),
        latency_ms=125,
        input_tokens=420,
        output_tokens=95,
        provider_request_id="resp-123",
    )


def _valid_gemini_response() -> LLMResponse:
    response = _valid_response()
    return replace(
        response,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        provider_request_id="gemini-response-123",
    )


def test_generic_llm_models_do_not_expose_provider_objects() -> None:
    request = LLMRequest("system", "user", "impact_analysis")
    response = LLMResponse("openai", "gpt-5.6-terra", "text", 10)

    assert request.system_prompt == "system"
    assert response.provider == "openai"
    assert response.input_tokens is None


class FakeResponses:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeSDKClient:
    def __init__(self, outcome: object) -> None:
        self.responses = FakeResponses(outcome)


def _openai_config() -> OpenAIConfig:
    return OpenAIConfig(
        api_key="test-key",
        model="gpt-5.6-terra",
        timeout_seconds=10,
        max_output_tokens=700,
        reasoning_effort="low",
    )


def test_openai_adapter_uses_responses_api_and_maps_output_usage() -> None:
    sdk_response = SimpleNamespace(
        id="resp-456",
        model="gpt-5.6-terra-2026-08-01",
        output_text="Grounded impact analysis.",
        usage=SimpleNamespace(input_tokens=111, output_tokens=44),
    )
    sdk_client = FakeSDKClient(sdk_response)
    client = OpenAIClient(_openai_config(), sdk_client)

    result = client.generate(LLMRequest("system", "task", "impact_analysis"))

    assert sdk_client.responses.calls == [
        {
            "model": "gpt-5.6-terra",
            "instructions": "system",
            "input": "task",
            "max_output_tokens": 700,
            "reasoning": {"effort": "low"},
            "store": False,
        }
    ]
    assert result.text == "Grounded impact analysis."
    assert result.model == "gpt-5.6-terra-2026-08-01"
    assert result.input_tokens == 111
    assert result.output_tokens == 44
    assert result.provider_request_id == "resp-456"


class FakeGeminiModels:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeGeminiSDKClient:
    def __init__(self, outcome: object) -> None:
        self.models = FakeGeminiModels(outcome)


def _gemini_config() -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-3.5-flash-lite",
        timeout_seconds=10,
        max_output_tokens=700,
    )


def test_gemini_adapter_maps_prompt_output_and_usage() -> None:
    sdk_response = SimpleNamespace(
        response_id="gemini-456",
        model_version="gemini-3.5-flash-lite-001",
        text="Grounded impact analysis.",
        usage_metadata=SimpleNamespace(
            prompt_token_count=101,
            candidates_token_count=37,
        ),
    )
    sdk_client = FakeGeminiSDKClient(sdk_response)
    client = GeminiClient(_gemini_config(), sdk_client)

    result = client.generate(LLMRequest("system", "task", "impact_analysis"))

    call = sdk_client.models.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["contents"] == "task"
    assert call["config"].system_instruction == "system"
    assert call["config"].max_output_tokens == 700
    assert result.provider == "gemini"
    assert result.model == "gemini-3.5-flash-lite-001"
    assert result.text == "Grounded impact analysis."
    assert result.input_tokens == 101
    assert result.output_tokens == 37
    assert result.provider_request_id == "gemini-456"


@pytest.mark.parametrize(
    ("status_code", "application_error"),
    [
        (401, LLMAuthenticationError),
        (400, LLMInvalidRequestError),
        (404, LLMModelError),
        (408, LLMTimeoutError),
        (429, LLMRateLimitError),
        (500, LLMTemporaryError),
    ],
)
def test_gemini_provider_errors_are_mapped(
    status_code: int,
    application_error: type[Exception],
) -> None:
    provider_error = gemini_errors.APIError(
        status_code,
        {"error": {"message": "provider failure"}},
    )
    client = GeminiClient(_gemini_config(), FakeGeminiSDKClient(provider_error))

    with pytest.raises(application_error):
        client.generate(LLMRequest("system", "task", "impact_analysis"))


def test_provider_factory_preserves_configured_order() -> None:
    clients = build_llm_clients(("openai", "gemini"))

    assert [client.provider for client in clients] == ["openai", "gemini"]
    assert clients[1].model == "gemini-2.5-flash-lite"


def _http_response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )


@pytest.mark.parametrize(
    ("provider_error", "application_error"),
    [
        (
            AuthenticationError(
                "invalid key",
                response=_http_response(401),
                body=None,
            ),
            LLMAuthenticationError,
        ),
        (
            BadRequestError(
                "invalid model parameter",
                response=_http_response(400),
                body=None,
            ),
            LLMInvalidRequestError,
        ),
        (
            RateLimitError(
                "rate limited",
                response=_http_response(429),
                body=None,
            ),
            LLMRateLimitError,
        ),
        (
            APITimeoutError(
                request=httpx.Request(
                    "POST", "https://api.openai.com/v1/responses"
                )
            ),
            LLMTimeoutError,
        ),
        (
            APIConnectionError(
                request=httpx.Request(
                    "POST", "https://api.openai.com/v1/responses"
                )
            ),
            LLMConnectionError,
        ),
        (
            NotFoundError(
                "model not found",
                response=_http_response(404),
                body=None,
            ),
            LLMModelError,
        ),
        (
            InternalServerError(
                "temporary server error",
                response=_http_response(500),
                body=None,
            ),
            LLMTemporaryError,
        ),
    ],
)
def test_openai_provider_errors_are_mapped(
    provider_error: Exception,
    application_error: type[Exception],
) -> None:
    client = OpenAIClient(_openai_config(), FakeSDKClient(provider_error))

    with pytest.raises(application_error):
        client.generate(LLMRequest("system", "task", "impact_analysis"))


def test_validation_rejects_empty_and_invented_source_paths() -> None:
    context = _prepared().context

    empty = validate_generation("", "impact_analysis", context)
    invented = validate_generation(
        (
            "## Impact Analysis\n\nThis proposed impact is described at "
            "payments/new_gateway_design.pdf, which is not part of the supplied "
            "evidence. Additional explanatory text makes this output long enough "
            "for the structural length check."
        ),
        "impact_analysis",
        context,
    )

    assert empty.valid is False
    assert "Generated text is empty." in empty.errors
    assert invented.valid is False
    assert any("payments/new_gateway_design.pdf" in error for error in invented.errors)


def test_validation_rejects_invented_explicit_chunk_id() -> None:
    evidence = [
        DocumentEvidence(
            chunk_id=f"doc-f69d08d8af4b-chunk-{index:04d}",
            document_id="doc-f69d08d8af4b",
            document_title="Requirement: Item-Level Partial Refunds",
            source_path="requirements/partial_refunds.md",
            section="Business rules",
            text="Partial-refund evidence.",
            retrieval_rank=index + 1,
        )
        for index in range(3)
    ]
    context = GroundedContext(
        requirement="Enable partial refunds",
        requested_section="impact_analysis",
        document_evidence=evidence,
        source_provenance=["requirements/partial_refunds.md"],
    )
    generated_text = (
        "## Impact Analysis\n\n"
        "The supplied requirement supports an item-level refund impact, as "
        "documented by doc-f69d08d8af4b-chunk-0000 and "
        "requirements/partial_refunds.md. However, the proposed design also "
        "cites doc-f69d08d8af4b-chunk-0003, which was not included in the "
        "grounded document evidence supplied for this generation request."
    )

    result = validate_generation(generated_text, "impact_analysis", context)

    assert result.valid is False
    assert result.errors == [
        "Generated text cites chunk IDs absent from GroundedContext: "
        "doc-f69d08d8af4b-chunk-0003"
    ]


class FakeLLMClient:
    def __init__(
        self,
        outcomes: list[LLMResponse | Exception],
        provider: str = "openai",
        model: str = "gpt-5.6-terra",
    ) -> None:
        self.outcomes = outcomes
        self.provider = provider
        self.model = model
        self.calls: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeRequestRepository:
    def __init__(self) -> None:
        self.statuses: list[GenerationRequestStatus] = []

    def update_status(
        self,
        request_id: str,
        status: GenerationRequestStatus,
    ) -> GenerationRequest:
        self.statuses.append(status)
        now = datetime.now(UTC)
        return GenerationRequest(
            request_id,
            "Enable partial refunds",
            "impact_analysis",
            1,
            status,
            now,
            now,
        )


class FakeRunRepository:
    def __init__(self) -> None:
        self.runs: dict[str, GenerationRun] = {}

    @property
    def created(self) -> int:
        return len(self.runs)

    @property
    def status(self) -> GenerationRunStatus:
        return list(self.runs.values())[-1].status

    def create(
        self,
        generation_request_id: str,
        provider: str,
        model: str,
        prompt_name: str,
        prompt_version: int,
    ) -> GenerationRun:
        run = self._run(
            f"run-{len(self.runs) + 1}",
            generation_request_id,
            provider,
            model,
            prompt_name,
            prompt_version,
        )
        self.runs[run.id] = run
        return run

    def mark_generated(self, run_id: str, response: LLMResponse) -> GenerationRun:
        run = replace(
            self.runs[run_id],
            provider=response.provider,
            model=response.model,
            generated_text=response.text,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            provider_request_id=response.provider_request_id,
            status=GenerationRunStatus.GENERATED,
            error_message=None,
        )
        self.runs[run_id] = run
        return run

    def mark_failed(
        self,
        run_id: str,
        error_message: str,
        response: LLMResponse | None = None,
    ) -> GenerationRun:
        changes: dict[str, object] = {
            "status": GenerationRunStatus.FAILED,
            "error_message": error_message,
        }
        if response is not None:
            changes.update(
                provider=response.provider,
                model=response.model,
                generated_text=response.text,
                latency_ms=response.latency_ms,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                provider_request_id=response.provider_request_id,
            )
        run = replace(self.runs[run_id], **changes)
        self.runs[run_id] = run
        return run

    def _run(
        self,
        run_id: str,
        request_id: str,
        provider: str,
        model: str,
        prompt_name: str = "impact_analysis",
        prompt_version: int = 1,
    ) -> GenerationRun:
        return GenerationRun(
            id=run_id,
            generation_request_id=request_id,
            provider=provider,
            model=model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            generated_text=None,
            latency_ms=None,
            input_tokens=None,
            output_tokens=None,
            provider_request_id=None,
            status=GenerationRunStatus.CREATED,
            error_message=None,
            created_at=datetime.now(UTC),
        )


def test_generation_service_succeeds_and_preserves_usage_metadata() -> None:
    llm = FakeLLMClient([_valid_response()])
    requests = FakeRequestRepository()
    runs = FakeRunRepository()
    service = GenerationService(llm, requests, runs, retry_delay_seconds=0)

    result = service.generate(_prepared())

    assert len(llm.calls) == 1
    assert llm.calls[0].system_prompt == "Use only supplied evidence."
    assert result.status is GenerationRequestStatus.GENERATED
    assert result.validation.valid is True
    assert result.input_tokens == 420
    assert result.output_tokens == 95
    assert result.provider_request_id == "resp-123"
    assert requests.statuses == [GenerationRequestStatus.GENERATED]
    assert runs.status is GenerationRunStatus.GENERATED


def test_transient_error_retries_once_then_succeeds() -> None:
    llm = FakeLLMClient([LLMRateLimitError("slow down"), _valid_response()])
    requests = FakeRequestRepository()
    runs = FakeRunRepository()
    delays: list[float] = []
    service = GenerationService(
        llm,
        requests,
        runs,
        max_retry_attempts=1,
        retry_delay_seconds=0.5,
        retry_jitter_seconds=0,
        sleep_function=delays.append,
    )

    result = service.generate(_prepared())

    assert result.status is GenerationRequestStatus.GENERATED
    assert len(llm.calls) == 2
    assert delays == [0.5]
    assert runs.created == 1


def test_deterministic_error_is_not_retried_and_marks_request_failed() -> None:
    llm = FakeLLMClient([LLMAuthenticationError("invalid key")])
    requests = FakeRequestRepository()
    runs = FakeRunRepository()
    service = GenerationService(
        llm,
        requests,
        runs,
        max_retry_attempts=3,
        retry_delay_seconds=0,
    )

    with pytest.raises(LLMAuthenticationError):
        service.generate(_prepared())

    assert len(llm.calls) == 1
    assert requests.statuses == [GenerationRequestStatus.FAILED]
    assert runs.status is GenerationRunStatus.FAILED


def test_empty_output_fails_validation_without_retry() -> None:
    empty_response = LLMResponse("openai", "gpt-5.6-terra", "", 20)
    llm = FakeLLMClient([empty_response])
    requests = FakeRequestRepository()
    runs = FakeRunRepository()
    service = GenerationService(llm, requests, runs, retry_delay_seconds=0)

    result = service.generate(_prepared())

    assert len(llm.calls) == 1
    assert result.status is GenerationRequestStatus.FAILED
    assert result.validation.valid is False
    assert requests.statuses == [GenerationRequestStatus.FAILED]
    assert runs.status is GenerationRunStatus.FAILED


def test_retryable_openai_failure_falls_back_to_gemini() -> None:
    openai = FakeLLMClient([LLMRateLimitError("OpenAI rate limit")])
    gemini = FakeLLMClient(
        [_valid_gemini_response()],
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    requests = FakeRequestRepository()
    runs = FakeRunRepository()
    service = GenerationService(
        [openai, gemini],
        requests,
        runs,
        max_retry_attempts=0,
        retry_delay_seconds=0,
    )

    result = service.generate(_prepared())

    assert result.provider == "gemini"
    assert result.status is GenerationRequestStatus.GENERATED
    assert [run.provider for run in runs.runs.values()] == ["openai", "gemini"]
    assert [run.status for run in runs.runs.values()] == [
        GenerationRunStatus.FAILED,
        GenerationRunStatus.GENERATED,
    ]
    assert requests.statuses == [GenerationRequestStatus.GENERATED]


def test_provider_fallback_is_observable(caplog: pytest.LogCaptureFixture) -> None:
    openai = FakeLLMClient([LLMRateLimitError("sensitive provider detail")])
    gemini = FakeLLMClient(
        [_valid_gemini_response()],
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    service = GenerationService(
        [openai, gemini],
        FakeRequestRepository(),
        FakeRunRepository(),
        max_retry_attempts=0,
        retry_delay_seconds=0,
        retry_jitter_seconds=0,
    )

    with caplog.at_level("WARNING"):
        result = service.generate(_prepared())

    fallback_records = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "provider_fallback_started"
    ]
    assert result.provider == "gemini"
    assert len(fallback_records) == 1
    assert fallback_records[0].fields == {
        "generation_request_id": "request-1",
        "from_provider": "openai",
        "to_provider": "gemini",
        "failure_category": "LLMRateLimitError",
        "fallback_occurred": True,
    }
    assert "sensitive provider detail" not in caplog.text


def test_deterministic_openai_failure_does_not_call_gemini() -> None:
    openai = FakeLLMClient([LLMAuthenticationError("invalid key")])
    gemini = FakeLLMClient(
        [_valid_gemini_response()],
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    requests = FakeRequestRepository()
    runs = FakeRunRepository()
    service = GenerationService(
        [openai, gemini],
        requests,
        runs,
        max_retry_attempts=0,
        retry_delay_seconds=0,
    )

    with pytest.raises(LLMAuthenticationError):
        service.generate(_prepared())

    assert len(gemini.calls) == 0
    assert runs.created == 1
    assert requests.statuses == [GenerationRequestStatus.FAILED]
