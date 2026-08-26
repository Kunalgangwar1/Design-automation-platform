"""Models passed through the deterministic ingestion pipeline."""

from dataclasses import dataclass, field
from enum import StrEnum


class SourceType(StrEnum):
    """Source formats currently understood by local loaders."""

    MARKDOWN = "markdown"
    TEXT = "text"


class DocumentType(StrEnum):
    """Enterprise document categories represented beneath ``data``."""

    REQUIREMENT = "requirement"
    ARCHITECTURE = "architecture"
    INTERFACE = "interface"
    EXISTING_SDD = "existing_sdd"
    OTHER = "other"


@dataclass(frozen=True)
class Document:
    """A normalized source document."""

    document_id: str
    title: str
    content: str
    source_path: str
    source_type: SourceType
    document_type: DocumentType
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentChunk:
    """A stable, ordered piece of a normalized document."""

    chunk_id: str
    document_id: str
    chunk_index: int
    section: str | None
    text: str
    metadata: dict[str, str] = field(default_factory=dict)
