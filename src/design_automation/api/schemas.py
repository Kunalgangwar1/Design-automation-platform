"""HTTP transport schemas kept separate from application/domain models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from design_automation.core import (
    DEFAULT_GRAPH_TRAVERSAL_DEPTH,
    MAX_REQUIREMENT_LENGTH,
    MAX_GRAPH_TRAVERSAL_DEPTH,
)
from design_automation.models import (
    GenerationRequestStatus,
    GenerationResult,
    GenerationRunStatus,
    PersistedGeneration,
    ValidationResult,
)


class GenerationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement: str = Field(min_length=1, max_length=MAX_REQUIREMENT_LENGTH)
    section: Literal["impact_analysis"] = "impact_analysis"
    graph_hops: int = Field(
        default=DEFAULT_GRAPH_TRAVERSAL_DEPTH,
        ge=1,
        le=MAX_GRAPH_TRAVERSAL_DEPTH,
    )

    @field_validator("requirement")
    @classmethod
    def requirement_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requirement must contain non-whitespace text")
        return value.strip()


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_application(cls, value: ValidationResult) -> "ValidationResponse":
        return cls(
            valid=value.valid,
            errors=list(value.errors),
            warnings=list(value.warnings),
        )


class GenerationResponse(BaseModel):
    request_id: str
    generation_run_id: str | None = None
    requested_section: str
    status: GenerationRequestStatus
    provider: str | None = None
    model: str | None = None
    generated_text: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_name: str | None = None
    prompt_version: int | None = None
    validation: ValidationResponse | None = None
    operational_warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_result(cls, result: GenerationResult) -> "GenerationResponse":
        return cls(
            request_id=result.generation_request_id,
            generation_run_id=result.generation_run_id,
            requested_section=result.requested_section,
            status=result.status,
            provider=result.provider,
            model=result.model,
            generated_text=result.generated_text,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            prompt_name=result.prompt_name,
            prompt_version=result.prompt_version,
            validation=ValidationResponse.from_application(result.validation),
            operational_warnings=list(result.operational_warnings),
        )

    @classmethod
    def from_persisted(
        cls,
        value: PersistedGeneration,
    ) -> "GenerationResponse":
        request = value.request
        run = value.latest_run
        generated = run.generated_text if run else None
        validation: ValidationResponse | None = None
        if run and run.status is GenerationRunStatus.GENERATED:
            validation = ValidationResponse(valid=True)
        elif run and run.generated_text is not None:
            validation = ValidationResponse(
                valid=False,
                errors=[run.error_message] if run.error_message else [],
            )
        return cls(
            request_id=request.id,
            generation_run_id=run.id if run else None,
            requested_section=request.requested_section,
            status=request.status,
            provider=run.provider if run else None,
            model=run.model if run else None,
            generated_text=generated,
            latency_ms=run.latency_ms if run else None,
            input_tokens=run.input_tokens if run else None,
            output_tokens=run.output_tokens if run else None,
            prompt_name=run.prompt_name if run else None,
            prompt_version=run.prompt_version if run else None,
            validation=validation,
        )


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessCheckResponse(BaseModel):
    status: Literal["ok", "unavailable"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, ReadinessCheckResponse]


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
