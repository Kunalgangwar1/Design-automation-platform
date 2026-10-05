"""Lightweight deterministic validation for generated SDD sections."""

import re
from collections.abc import Sequence

from design_automation.models import GroundedContext, ValidationResult


SOURCE_PATH_PATTERN = re.compile(
    r"(?<![\w.-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+"
    r"\.(?:md|txt|pdf|docx))(?![\w.-])",
    re.IGNORECASE,
)
CHUNK_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(doc-[A-Za-z0-9_-]+-chunk-\d{4,})"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
HEADING_PATTERN = re.compile(r"^#{1,4}\s+(.+?)\s*$", re.MULTILINE)
PROVIDER_ERROR_PATTERN = re.compile(
    r"\b(?:internal server error|authentication error|rate limit exceeded|"
    r"provider error)\b",
    re.IGNORECASE,
)


def validate_generation(
    text: str,
    requested_section: str,
    context: GroundedContext,
    minimum_characters: int = 120,
) -> ValidationResult:
    """Validate structure and provenance without claiming factual correctness."""

    errors: list[str] = []
    warnings: list[str] = []
    stripped = text.strip()

    if not stripped:
        errors.append("Generated text is empty.")
    elif len(stripped) < minimum_characters:
        errors.append(
            f"Generated text is shorter than {minimum_characters} characters."
        )

    if PROVIDER_ERROR_PATTERN.search(stripped):
        errors.append("Generated text appears to contain a provider error message.")

    allowed_sources = {
        source.casefold() for source in context.source_provenance
    } | {
        evidence.source_path.casefold()
        for evidence in context.document_evidence
    }
    cited_sources = _deduplicate(SOURCE_PATH_PATTERN.findall(stripped))
    unknown_sources = [
        source for source in cited_sources if source.casefold() not in allowed_sources
    ]
    if unknown_sources:
        errors.append(
            "Generated text cites sources absent from GroundedContext: "
            + ", ".join(unknown_sources)
        )

    allowed_chunk_ids = {
        evidence.chunk_id for evidence in context.document_evidence
    }
    cited_chunk_ids = _deduplicate(CHUNK_ID_PATTERN.findall(stripped))
    unknown_chunk_ids = [
        chunk_id
        for chunk_id in cited_chunk_ids
        if chunk_id not in allowed_chunk_ids
    ]
    if unknown_chunk_ids:
        errors.append(
            "Generated text cites chunk IDs absent from GroundedContext: "
            + ", ".join(unknown_chunk_ids)
        )

    headings = HEADING_PATTERN.findall(stripped)
    section_words = set(re.findall(r"[a-z0-9]+", requested_section.casefold()))
    if headings and section_words:
        matching_heading = any(
            section_words.issubset(
                set(re.findall(r"[a-z0-9]+", heading.casefold()))
            )
            for heading in headings
        )
        if not matching_heading:
            warnings.append(
                "No Markdown heading clearly matches the requested section "
                f"'{requested_section}'."
            )

    return ValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )


def _deduplicate(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
