"""Deterministic known-entity matching for retrieved chunks."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from design_automation.graph.schema import OMS_ENTITIES, OMS_RELATIONSHIPS
from design_automation.models import RetrievalResult


def _build_known_entity_catalog() -> tuple[str, ...]:
    """Collect every canonical entity name declared by the Day-5 graph seed."""

    names = [entity.name for entity in OMS_ENTITIES]
    for relationship in OMS_RELATIONSHIPS:
        names.extend((relationship.source_name, relationship.target_name))

    catalog: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            catalog.append(name)
    return tuple(catalog)


KNOWN_ENTITY_NAMES = _build_known_entity_catalog()


@dataclass(frozen=True)
class EntityMatch:
    """One explainable entity-name match in retrieved chunk evidence."""

    chunk_id: str
    field: Literal["section", "text"]
    entity_name: str


def _compile_patterns(
    known_names: Sequence[str],
) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Compile case-insensitive patterns while retaining canonical names."""

    return tuple(
        (
            name,
            re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE),
        )
        for name in known_names
    )


KNOWN_ENTITY_PATTERNS = _compile_patterns(KNOWN_ENTITY_NAMES)


def _matching_names(
    text: str,
    patterns: Sequence[tuple[str, re.Pattern[str]]],
) -> list[str]:
    """Return canonical names ordered by their first occurrence in the text."""

    matches: list[tuple[int, int, str]] = []
    for catalog_index, (name, pattern) in enumerate(patterns):
        match = pattern.search(text)
        if match:
            matches.append((match.start(), catalog_index, name))
    return [name for _, _, name in sorted(matches)]


def find_known_entity_matches(
    results: Sequence[RetrievalResult],
    known_names: Sequence[str] = KNOWN_ENTITY_NAMES,
) -> list[EntityMatch]:
    """Return every canonical entity match with its chunk and evidence field."""

    patterns = (
        KNOWN_ENTITY_PATTERNS
        if known_names is KNOWN_ENTITY_NAMES
        else _compile_patterns(known_names)
    )
    matches: list[EntityMatch] = []

    for result in results:
        evidence_fields: tuple[tuple[Literal["section", "text"], str], ...] = (
            ("section", result.section or ""),
            ("text", result.text),
        )
        for field, evidence in evidence_fields:
            for name in _matching_names(evidence, patterns):
                matches.append(EntityMatch(result.chunk_id, field, name))
    return matches


def detect_known_entities(
    results: Sequence[RetrievalResult],
    known_names: Sequence[str] = KNOWN_ENTITY_NAMES,
    *,
    debug: bool = False,
) -> list[str]:
    """Find every known entity named in retrieved section headings or chunk text."""

    detected: list[str] = []
    seen: set[str] = set()

    # The user query is intentionally not part of matching. Retrieved evidence
    # alone supplies graph seeds, with the heading inspected before full text.
    for match in find_known_entity_matches(results, known_names):
        if debug:
            print(
                f"Entity match: chunk={match.chunk_id} "
                f"field={match.field} entity={match.entity_name}"
            )
        key = match.entity_name.casefold()
        if key not in seen:
            seen.add(key)
            detected.append(match.entity_name)
    return detected
