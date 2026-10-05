"""SQLAlchemy persistence records for Day-6 workflow state."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from design_automation.models import GenerationRequestStatus, GenerationRunStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative metadata shared by application tables."""


class PromptTemplateRecord(Base):
    """Versioned prompt template persisted in PostgreSQL."""

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompt_template_name_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    task_template: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )


class GenerationRequestRecord(Base):
    """Transactional state for one requested SDD section."""

    __tablename__ = "generation_requests"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    requested_section: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_template_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_templates.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=GenerationRequestStatus.CREATED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )


class GenerationRunRecord(Base):
    """One provider generation run; requests may have many runs."""

    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    generation_request_id: Mapped[str] = mapped_column(
        ForeignKey("generation_requests.id"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[int] = mapped_column(nullable=False)
    generated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=GenerationRunStatus.CREATED.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
