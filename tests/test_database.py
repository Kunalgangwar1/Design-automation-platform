"""Repository tests use SQLite and do not require live PostgreSQL."""

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from design_automation.database.repositories import (
    GenerationRequestRepository,
    GenerationRunRepository,
    PromptTemplateRepository,
)
from design_automation.database.schema import Base, PromptTemplateRecord
from design_automation.database.seed import initial_impact_analysis_template
from design_automation.models import (
    GenerationRequestStatus,
    GenerationRunStatus,
    LLMResponse,
    PromptTemplate,
)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_prompt_seed_is_idempotent_and_active_template_is_queryable() -> None:
    with _session() as session:
        repository = PromptTemplateRepository(session)
        first = repository.seed(initial_impact_analysis_template())
        second = repository.seed(initial_impact_analysis_template())

        count = session.scalar(select(func.count()).select_from(PromptTemplateRecord))
        active = repository.find_active_by_name("impact_analysis")

    assert first.id == second.id
    assert count == 1
    assert active is not None
    assert active.id == first.id
    assert active.version == 1


def test_new_active_template_deactivates_older_version() -> None:
    with _session() as session:
        repository = PromptTemplateRepository(session)
        repository.seed(initial_impact_analysis_template())
        version_two = repository.seed(
            PromptTemplate(
                id=None,
                name="impact_analysis",
                version=2,
                system_prompt="Grounded system prompt v2",
                task_template="{requirement} {document_evidence} {graph_evidence}",
            )
        )

        active = repository.find_active_by_name("impact_analysis")
        old = repository.get_version("impact_analysis", 1)

    assert active == version_two
    assert old is not None
    assert old.is_active is False


def test_generation_request_transitions_created_to_context_ready() -> None:
    with _session() as session:
        prompt_repository = PromptTemplateRepository(session)
        template = prompt_repository.seed(initial_impact_analysis_template())
        assert template.id is not None
        repository = GenerationRequestRepository(session)

        created = repository.create(
            "Enable partial refunds",
            "impact_analysis",
            template.id,
        )
        ready = repository.update_status(
            created.id,
            GenerationRequestStatus.CONTEXT_READY,
        )
        loaded = repository.get(created.id)

    assert created.status is GenerationRequestStatus.CREATED
    assert ready.status is GenerationRequestStatus.CONTEXT_READY
    assert loaded == ready


def test_generation_request_supports_multiple_persisted_runs() -> None:
    with _session() as session:
        prompt_repository = PromptTemplateRepository(session)
        template = prompt_repository.seed(initial_impact_analysis_template())
        assert template.id is not None
        request_repository = GenerationRequestRepository(session)
        request = request_repository.create(
            "Enable partial refunds",
            "impact_analysis",
            template.id,
        )
        request_repository.update_status(
            request.id,
            GenerationRequestStatus.CONTEXT_READY,
        )
        run_repository = GenerationRunRepository(session)

        first = run_repository.create(
            request.id,
            "openai",
            "gpt-5.6-terra",
            "impact_analysis",
            1,
        )
        first = run_repository.mark_generated(
            first.id,
            LLMResponse(
                "openai",
                "gpt-5.6-terra",
                "Generated impact analysis",
                100,
                30,
                20,
                "resp-1",
            ),
        )
        second = run_repository.create(
            request.id,
            "openai",
            "gpt-5.6-terra",
            "impact_analysis",
            1,
        )
        second = run_repository.mark_failed(second.id, "temporary failure")
        runs = run_repository.list_for_request(request.id)
        latest = run_repository.latest_for_request(request.id)

    assert len(runs) == 2
    assert first.status is GenerationRunStatus.GENERATED
    assert first.input_tokens == 30
    assert first.provider_request_id == "resp-1"
    assert second.status is GenerationRunStatus.FAILED
    assert latest == second
