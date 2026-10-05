"""Small SQLAlchemy repositories for prompt and request persistence."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from design_automation.database.schema import (
    GenerationRequestRecord,
    GenerationRunRecord,
    PromptTemplateRecord,
)
from design_automation.models import (
    GenerationRequest,
    GenerationRequestStatus,
    GenerationRun,
    GenerationRunStatus,
    LLMResponse,
    PromptTemplate,
)


def _prompt_model(record: PromptTemplateRecord) -> PromptTemplate:
    return PromptTemplate(
        id=record.id,
        name=record.name,
        version=record.version,
        system_prompt=record.system_prompt,
        task_template=record.task_template,
        is_active=record.is_active,
        created_at=record.created_at,
    )


def _request_model(record: GenerationRequestRecord) -> GenerationRequest:
    return GenerationRequest(
        id=record.id,
        requirement_text=record.requirement_text,
        requested_section=record.requested_section,
        prompt_template_id=record.prompt_template_id,
        status=GenerationRequestStatus(record.status),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run_model(record: GenerationRunRecord) -> GenerationRun:
    return GenerationRun(
        id=record.id,
        generation_request_id=record.generation_request_id,
        provider=record.provider,
        model=record.model,
        prompt_name=record.prompt_name,
        prompt_version=record.prompt_version,
        generated_text=record.generated_text,
        latency_ms=record.latency_ms,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        provider_request_id=record.provider_request_id,
        status=GenerationRunStatus(record.status),
        error_message=record.error_message,
        created_at=record.created_at,
    )


class PromptTemplateRepository:
    """Persist and select versioned prompt templates."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def seed(self, template: PromptTemplate) -> PromptTemplate:
        record = self._session.scalar(
            select(PromptTemplateRecord).where(
                PromptTemplateRecord.name == template.name,
                PromptTemplateRecord.version == template.version,
            )
        )
        if record is None:
            record = PromptTemplateRecord(
                name=template.name,
                version=template.version,
                system_prompt=template.system_prompt,
                task_template=template.task_template,
                is_active=template.is_active,
            )
            self._session.add(record)
        else:
            record.system_prompt = template.system_prompt
            record.task_template = template.task_template
            record.is_active = template.is_active

        if template.is_active:
            self._session.execute(
                update(PromptTemplateRecord)
                .where(
                    PromptTemplateRecord.name == template.name,
                    PromptTemplateRecord.version != template.version,
                )
                .values(is_active=False)
            )
        self._session.commit()
        self._session.refresh(record)
        return _prompt_model(record)

    def find_active_by_name(self, name: str) -> PromptTemplate | None:
        record = self._session.scalar(
            select(PromptTemplateRecord)
            .where(
                PromptTemplateRecord.name == name,
                PromptTemplateRecord.is_active.is_(True),
            )
            .order_by(PromptTemplateRecord.version.desc())
        )
        return _prompt_model(record) if record else None

    def get_version(self, name: str, version: int) -> PromptTemplate | None:
        record = self._session.scalar(
            select(PromptTemplateRecord).where(
                PromptTemplateRecord.name == name,
                PromptTemplateRecord.version == version,
            )
        )
        return _prompt_model(record) if record else None


class GenerationRequestRepository:
    """Persist and update generation workflow state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        requirement_text: str,
        requested_section: str,
        prompt_template_id: int,
    ) -> GenerationRequest:
        record = GenerationRequestRecord(
            requirement_text=requirement_text,
            requested_section=requested_section,
            prompt_template_id=prompt_template_id,
            status=GenerationRequestStatus.CREATED.value,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return _request_model(record)

    def update_status(
        self,
        request_id: str,
        status: GenerationRequestStatus,
    ) -> GenerationRequest:
        record = self._session.get(GenerationRequestRecord, request_id)
        if record is None:
            raise LookupError(f"Generation request not found: {request_id}")
        record.status = status.value
        record.updated_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(record)
        return _request_model(record)

    def get(self, request_id: str) -> GenerationRequest | None:
        record = self._session.get(GenerationRequestRecord, request_id)
        return _request_model(record) if record else None


class GenerationRunRepository:
    """Persist separate provider runs for retries or future fallback."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        generation_request_id: str,
        provider: str,
        model: str,
        prompt_name: str,
        prompt_version: int,
    ) -> GenerationRun:
        record = GenerationRunRecord(
            generation_request_id=generation_request_id,
            provider=provider,
            model=model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            status=GenerationRunStatus.CREATED.value,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return _run_model(record)

    def mark_generated(
        self,
        run_id: str,
        response: LLMResponse,
    ) -> GenerationRun:
        record = self._require_record(run_id)
        self._apply_response(record, response)
        record.status = GenerationRunStatus.GENERATED.value
        record.error_message = None
        self._session.commit()
        self._session.refresh(record)
        return _run_model(record)

    def mark_failed(
        self,
        run_id: str,
        error_message: str,
        response: LLMResponse | None = None,
    ) -> GenerationRun:
        record = self._require_record(run_id)
        if response is not None:
            self._apply_response(record, response)
        record.status = GenerationRunStatus.FAILED.value
        record.error_message = error_message
        self._session.commit()
        self._session.refresh(record)
        return _run_model(record)

    def list_for_request(self, request_id: str) -> list[GenerationRun]:
        records = self._session.scalars(
            select(GenerationRunRecord)
            .where(GenerationRunRecord.generation_request_id == request_id)
            .order_by(GenerationRunRecord.created_at, GenerationRunRecord.id)
        ).all()
        return [_run_model(record) for record in records]

    def latest_for_request(self, request_id: str) -> GenerationRun | None:
        record = self._session.scalar(
            select(GenerationRunRecord)
            .where(GenerationRunRecord.generation_request_id == request_id)
            .order_by(
                GenerationRunRecord.created_at.desc(),
                GenerationRunRecord.id.desc(),
            )
            .limit(1)
        )
        return _run_model(record) if record else None

    def _require_record(self, run_id: str) -> GenerationRunRecord:
        record = self._session.get(GenerationRunRecord, run_id)
        if record is None:
            raise LookupError(f"Generation run not found: {run_id}")
        return record

    @staticmethod
    def _apply_response(
        record: GenerationRunRecord,
        response: LLMResponse,
    ) -> None:
        record.provider = response.provider
        record.model = response.model
        record.generated_text = response.text
        record.latency_ms = response.latency_ms
        record.input_tokens = response.input_tokens
        record.output_tokens = response.output_tokens
        record.provider_request_id = response.provider_request_id
