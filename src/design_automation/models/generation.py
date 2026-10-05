"""Stable application models at the generation-preparation boundary."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class GenerationRequestStatus(StrEnum):
    """Lifecycle states for an SDD generation request."""

    CREATED = "CREATED"
    CONTEXT_READY = "CONTEXT_READY"
    GENERATED = "GENERATED"
    FAILED = "FAILED"


class GenerationRunStatus(StrEnum):
    """Lifecycle states for one provider generation run."""

    CREATED = "CREATED"
    GENERATED = "GENERATED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class DocumentEvidence:
    """One retrieval result converted into model-independent evidence."""

    chunk_id: str
    document_id: str
    document_title: str
    source_path: str
    section: str | None
    text: str
    retrieval_rank: int
    fusion_score: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEvidence:
    """One explicit directed relationship from graph enrichment."""

    source_entity: str
    source_type: str
    relationship: str
    target_entity: str
    target_type: str

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.source_entity,
            self.source_type,
            self.relationship,
            self.target_entity,
            self.target_type,
        )


@dataclass(frozen=True)
class GroundedContext:
    """Document and graph evidence prepared for a requested SDD section."""

    requirement: str
    requested_section: str
    document_evidence: list[DocumentEvidence] = field(default_factory=list)
    graph_evidence: list[GraphEvidence] = field(default_factory=list)
    source_provenance: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PromptTemplate:
    """Versioned prompt content independent of its persistence record."""

    id: int | None
    name: str
    version: int
    system_prompt: str
    task_template: str
    is_active: bool = True
    created_at: datetime | None = None


@dataclass(frozen=True)
class GenerationRequest:
    """Persistent workflow state represented outside SQLAlchemy."""

    id: str
    requirement_text: str
    requested_section: str
    prompt_template_id: int
    status: GenerationRequestStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RenderedPrompt:
    """A model-ready prompt that has not been sent to any model."""

    system_prompt: str
    task_prompt: str
    prompt_name: str
    prompt_version: int
    provenance_summary: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PreparedGenerationRequest:
    """Day-6 output and future Day-7 generation input."""

    request_id: str
    requested_section: str
    prompt_name: str
    prompt_version: int
    context: GroundedContext
    rendered_prompt: RenderedPrompt
    detected_entities: list[str]
    status: GenerationRequestStatus
    operational_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LLMRequest:
    """Provider-independent input passed to an LLM client."""

    system_prompt: str
    user_prompt: str
    requested_section: str


@dataclass(frozen=True)
class LLMResponse:
    """Provider response with SDK-specific objects removed."""

    provider: str
    model: str
    text: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_request_id: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Deterministic structural checks, not semantic correctness proof."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GenerationRun:
    """One persisted provider invocation associated with a request."""

    id: str
    generation_request_id: str
    provider: str
    model: str
    prompt_name: str
    prompt_version: int
    generated_text: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    provider_request_id: str | None
    status: GenerationRunStatus
    error_message: str | None
    created_at: datetime


@dataclass(frozen=True)
class GenerationResult:
    """Validated application result returned by the generation service."""

    generation_request_id: str
    generation_run_id: str
    requested_section: str
    provider: str
    model: str
    generated_text: str
    prompt_name: str
    prompt_version: int
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    provider_request_id: str | None
    validation: ValidationResult
    status: GenerationRequestStatus
    operational_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PersistedGeneration:
    """Stored request state plus its latest provider run, when one exists."""

    request: GenerationRequest
    latest_run: GenerationRun | None = None
