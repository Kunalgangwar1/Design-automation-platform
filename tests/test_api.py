"""Offline API boundary tests with an overridden application facade."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from design_automation.api.app import create_app
from design_automation.api.dependencies import get_generation_application_service
from design_automation.api.dependencies import ReadinessStatus, get_readiness_status
from design_automation.api.middleware import REQUEST_ID_HEADER, select_request_id
from design_automation.core import MAX_REQUIREMENT_LENGTH
from design_automation.generation import (
    GenerationNotFoundError,
    LLMTemporaryError,
)
from design_automation.models import (
    GenerationRequest,
    GenerationRequestStatus,
    GenerationResult,
    GenerationRun,
    GenerationRunStatus,
    PersistedGeneration,
    ValidationResult,
)


def _result() -> GenerationResult:
    return GenerationResult(
        generation_request_id="request-123",
        generation_run_id="run-456",
        requested_section="impact_analysis",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        generated_text="## Impact Analysis\n\nGrounded partial-refund impact analysis.",
        prompt_name="impact_analysis",
        prompt_version=1,
        latency_ms=275,
        input_tokens=900,
        output_tokens=180,
        provider_request_id="provider-response-id",
        validation=ValidationResult(valid=True, warnings=["Human review required."]),
        status=GenerationRequestStatus.GENERATED,
    )


def _persisted() -> PersistedGeneration:
    now = datetime.now(UTC)
    request = GenerationRequest(
        id="request-123",
        requirement_text="Enable partial refunds",
        requested_section="impact_analysis",
        prompt_template_id=1,
        status=GenerationRequestStatus.GENERATED,
        created_at=now,
        updated_at=now,
    )
    run = GenerationRun(
        id="run-456",
        generation_request_id=request.id,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        prompt_name="impact_analysis",
        prompt_version=1,
        generated_text="## Impact Analysis\n\nGrounded persisted result.",
        latency_ms=275,
        input_tokens=900,
        output_tokens=180,
        provider_request_id="provider-response-id",
        status=GenerationRunStatus.GENERATED,
        error_message=None,
        created_at=now,
    )
    return PersistedGeneration(request, run)


class FakeGenerationApplicationService:
    def __init__(self) -> None:
        self.generate_calls: list[tuple[str, str, int]] = []
        self.get_calls: list[str] = []
        self.generate_error: Exception | None = None

    def generate(
        self,
        requirement: str,
        requested_section: str,
        graph_hops: int,
    ) -> GenerationResult:
        self.generate_calls.append(
            (requirement, requested_section, graph_hops)
        )
        if self.generate_error is not None:
            raise self.generate_error
        return _result()

    def get_generation(self, request_id: str) -> PersistedGeneration:
        self.get_calls.append(request_id)
        if request_id == "missing":
            raise GenerationNotFoundError(request_id)
        return _persisted()


def _app_with(service: FakeGenerationApplicationService):
    app = create_app(resources_factory=lambda: None)
    app.dependency_overrides[get_generation_application_service] = lambda: service
    return app


def test_health_returns_200_without_application_service() -> None:
    app = create_app(resources_factory=lambda: None)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_closes_shared_resources() -> None:
    class StubResources:
        closed = False

        def close(self) -> None:
            self.closed = True

    resources = StubResources()
    app = create_app(resources_factory=lambda: resources)  # type: ignore[arg-type]

    with TestClient(app) as client:
        assert app.state.resources is resources
        assert client.get("/health").status_code == 200
        assert resources.closed is False

    assert resources.closed is True


def test_valid_post_returns_created_and_serializes_safe_result() -> None:
    service = FakeGenerationApplicationService()

    with TestClient(_app_with(service)) as client:
        response = client.post(
            "/api/v1/generations",
            json={
                "requirement": (
                    "Enable partial refunds for individual items of completed orders."
                ),
                "section": "impact_analysis",
                "graph_hops": 2,
            },
        )

    assert response.status_code == 201
    assert service.generate_calls == [
        (
            "Enable partial refunds for individual items of completed orders.",
            "impact_analysis",
            2,
        )
    ]
    assert response.json() == {
        "request_id": "request-123",
        "generation_run_id": "run-456",
        "requested_section": "impact_analysis",
        "status": "GENERATED",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "generated_text": (
            "## Impact Analysis\n\nGrounded partial-refund impact analysis."
        ),
        "latency_ms": 275,
        "input_tokens": 900,
        "output_tokens": 180,
        "prompt_name": "impact_analysis",
        "prompt_version": 1,
        "validation": {
            "valid": True,
            "errors": [],
            "warnings": ["Human review required."],
        },
        "operational_warnings": [],
    }
    assert "provider-response-id" not in response.text


def test_request_schema_rejects_missing_requirement_and_extra_fields() -> None:
    service = FakeGenerationApplicationService()

    with TestClient(_app_with(service)) as client:
        missing = client.post(
            "/api/v1/generations",
            json={"section": "impact_analysis", "graph_hops": 2},
        )
        extra = client.post(
            "/api/v1/generations",
            json={
                "requirement": "Enable partial refunds",
                "section": "impact_analysis",
                "graph_hops": 2,
                "api_key": "must-not-be-accepted",
            },
        )
        unsupported_section = client.post(
            "/api/v1/generations",
            json={
                "requirement": "Enable partial refunds",
                "section": "risks",
                "graph_hops": 2,
            },
        )

    assert missing.status_code == 422
    assert extra.status_code == 422
    assert unsupported_section.status_code == 422
    assert service.generate_calls == []


def test_empty_requirement_is_rejected() -> None:
    service = FakeGenerationApplicationService()

    with TestClient(_app_with(service)) as client:
        response = client.post(
            "/api/v1/generations",
            json={
                "requirement": "   ",
                "section": "impact_analysis",
                "graph_hops": 2,
            },
        )

    assert response.status_code == 422
    assert service.generate_calls == []


def test_invalid_graph_hops_are_rejected() -> None:
    service = FakeGenerationApplicationService()

    with TestClient(_app_with(service)) as client:
        too_low = client.post(
            "/api/v1/generations",
            json={"requirement": "Partial refunds", "graph_hops": 0},
        )
        too_high = client.post(
            "/api/v1/generations",
            json={"requirement": "Partial refunds", "graph_hops": 4},
        )

    assert too_low.status_code == 422
    assert too_high.status_code == 422
    assert service.generate_calls == []


def test_get_existing_generation_returns_latest_persisted_result() -> None:
    service = FakeGenerationApplicationService()

    with TestClient(_app_with(service)) as client:
        response = client.get("/api/v1/generations/request-123")

    assert response.status_code == 200
    assert service.get_calls == ["request-123"]
    assert response.json()["request_id"] == "request-123"
    assert response.json()["generation_run_id"] == "run-456"
    assert response.json()["generated_text"].endswith("Grounded persisted result.")


def test_get_unknown_generation_returns_404() -> None:
    service = FakeGenerationApplicationService()

    with TestClient(_app_with(service)) as client:
        response = client.get("/api/v1/generations/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "generation_not_found"
    assert response.json()["error"]["message"] == (
        "The requested generation does not exist."
    )
    assert response.json()["error"]["request_id"] == response.headers[
        REQUEST_ID_HEADER
    ]


def test_provider_failure_maps_to_safe_error_without_raw_details() -> None:
    service = FakeGenerationApplicationService()
    secret = "OPENAI_API_KEY=do-not-leak"
    service.generate_error = LLMTemporaryError(
        f"provider traceback containing {secret}"
    )

    with TestClient(_app_with(service), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/generations",
            json={"requirement": "Enable partial refunds", "graph_hops": 2},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "generation_provider_unavailable"
    assert secret not in response.text
    assert "traceback" not in response.text.casefold()


def test_unexpected_failure_maps_to_safe_500_without_raw_details() -> None:
    service = FakeGenerationApplicationService()
    secret = "DATABASE_URL=postgresql://secret"
    service.generate_error = RuntimeError(f"internal traceback with {secret}")

    with TestClient(_app_with(service), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/generations",
            json={"requirement": "Enable partial refunds", "graph_hops": 2},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == (
        "An unexpected internal error occurred."
    )
    assert response.json()["error"]["request_id"] == response.headers[
        REQUEST_ID_HEADER
    ]
    assert secret not in response.text


def test_request_id_is_generated_and_returned() -> None:
    with TestClient(_app_with(FakeGenerationApplicationService())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


def test_safe_incoming_request_id_is_preserved() -> None:
    incoming = "support-case-123"
    with TestClient(_app_with(FakeGenerationApplicationService())) as client:
        response = client.get("/health", headers={REQUEST_ID_HEADER: incoming})

    assert response.headers[REQUEST_ID_HEADER] == incoming


def test_unsafe_incoming_request_id_is_replaced() -> None:
    unsafe = "bad request id with spaces"

    selected = select_request_id(unsafe)

    assert selected != unsafe
    assert len(selected) == 36


def test_requirement_size_limit_is_enforced() -> None:
    service = FakeGenerationApplicationService()
    with TestClient(_app_with(service)) as client:
        response = client.post(
            "/api/v1/generations",
            json={"requirement": "x" * (MAX_REQUIREMENT_LENGTH + 1)},
        )

    assert response.status_code == 422
    assert service.generate_calls == []


def test_readiness_reports_success_without_calling_generation_service() -> None:
    app = create_app(resources_factory=lambda: None)
    app.dependency_overrides[get_readiness_status] = lambda: ReadinessStatus(
        ready=True,
        checks={"postgresql": True, "neo4j": True, "faiss": True},
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "postgresql": {"status": "ok"},
            "neo4j": {"status": "ok"},
            "faiss": {"status": "ok"},
        },
    }


def test_readiness_reports_safe_failure() -> None:
    app = create_app(resources_factory=lambda: None)
    app.dependency_overrides[get_readiness_status] = lambda: ReadinessStatus(
        ready=False,
        checks={"postgresql": True, "neo4j": False, "faiss": True},
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["neo4j"] == {"status": "unavailable"}
    assert "password" not in response.text.casefold()
