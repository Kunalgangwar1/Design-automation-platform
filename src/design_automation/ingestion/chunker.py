"""Structure-aware, deterministic document chunking."""

import re
from dataclasses import dataclass

from design_automation.core import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE
from design_automation.ingestion.metadata import is_recognized_metadata_block
from design_automation.models import Document, DocumentChunk, SourceType


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
BOUNDARIES = ("\n\n", "\n", ". ", " ")


@dataclass(frozen=True)
class ChunkingConfig:
    """Character-based chunk limits used before tokenizer-specific work exists."""

    max_chars: int = DEFAULT_CHUNK_SIZE
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP

    def __post_init__(self) -> None:
        if self.max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if self.overlap_chars < 0:
            raise ValueError("overlap_chars cannot be negative")
        if self.overlap_chars >= self.max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")


@dataclass(frozen=True)
class _Section:
    heading: str | None
    heading_line: str | None
    body: str


def _markdown_sections(content: str) -> list[_Section]:
    sections: list[_Section] = []
    heading: str | None = None
    heading_line: str | None = None
    body_lines: list[str] = []
    inside_code_fence = False

    def append_section() -> None:
        body = "\n".join(body_lines).strip()
        if body:
            sections.append(_Section(heading, heading_line, body))

    for line in content.splitlines():
        if line.lstrip().startswith("```"):
            inside_code_fence = not inside_code_fence

        match = None if inside_code_fence else HEADING_PATTERN.match(line)
        if match:
            append_section()
            heading = match.group(2).strip()
            heading_line = line
            body_lines = []
        else:
            body_lines.append(line)

    append_section()
    return sections


def _find_split_point(text: str, start: int, limit: int) -> int:
    """Prefer paragraph, line, sentence, then word boundaries near the limit."""

    minimum = start + max(1, (limit - start) // 2)
    window = text[start:limit]
    for boundary in BOUNDARIES:
        position = window.rfind(boundary)
        if position >= 0:
            split_point = start + position + len(boundary)
            if split_point >= minimum:
                return split_point
    return limit


def _split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text.strip()] if text.strip() else []

    pieces: list[str] = []
    start = 0
    while start < len(text):
        limit = min(start + max_chars, len(text))
        end = len(text) if limit == len(text) else _find_split_point(text, start, limit)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        if overlap_chars == 0:
            start = end
            continue

        desired_start = max(start + 1, end - overlap_chars)
        search_start = max(start + 1, end - (overlap_chars * 2))
        overlap_window = text[search_start:desired_start]
        overlap_start = desired_start
        for boundary in BOUNDARIES:
            position = overlap_window.rfind(boundary)
            if position >= 0:
                overlap_start = search_start + position + len(boundary)
                break
        start = min(overlap_start, end - 1)
    return pieces


def _section_chunks(section: _Section, config: ChunkingConfig) -> list[str]:
    prefix = section.heading_line or ""
    complete_section = "\n\n".join(part for part in (prefix, section.body) if part)
    if len(complete_section) <= config.max_chars:
        return [complete_section] if complete_section else []

    prefix_with_spacing = f"{prefix}\n\n" if prefix else ""
    body_limit = config.max_chars - len(prefix_with_spacing)
    if body_limit <= config.overlap_chars:
        # An unusually long heading cannot be repeated within the configured limit.
        return _split_text(complete_section, config.max_chars, config.overlap_chars)

    body_pieces = _split_text(section.body, body_limit, config.overlap_chars)
    return [f"{prefix_with_spacing}{piece}".strip() for piece in body_pieces]


def _is_metadata_only_document_header(
    section: _Section,
    document: Document,
) -> bool:
    return (
        section.heading_line is not None
        and section.heading_line.startswith("# ")
        and section.heading == document.title
        and is_recognized_metadata_block(section.body)
    )


def chunk_document(
    document: Document,
    config: ChunkingConfig | None = None,
) -> list[DocumentChunk]:
    """Chunk one document while retaining source order and metadata."""

    chunking = config or ChunkingConfig()
    if document.source_type is SourceType.MARKDOWN:
        sections = _markdown_sections(document.content)
    else:
        sections = [_Section(None, None, document.content)]

    chunks: list[DocumentChunk] = []
    for section in sections:
        if _is_metadata_only_document_header(section, document):
            continue
        for text in _section_chunks(section, chunking):
            chunk_index = len(chunks)
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{document.document_id}-chunk-{chunk_index:04d}",
                    document_id=document.document_id,
                    chunk_index=chunk_index,
                    section=section.heading,
                    text=text,
                    metadata=dict(document.metadata),
                )
            )
    return chunks
