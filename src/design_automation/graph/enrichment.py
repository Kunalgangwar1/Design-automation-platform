"""Build structured graph context from deterministic entity seeds."""

from __future__ import annotations

from typing import Protocol

from design_automation.core import (
    DEFAULT_GRAPH_TRAVERSAL_DEPTH,
    MAX_GRAPH_TRAVERSAL_DEPTH,
)
from design_automation.models import GraphContext, GraphEntity, GraphRelationship


class EnrichmentRepository(Protocol):
    def get_entity(self, name: str) -> GraphEntity | None: ...

    def get_related_relationships(
        self,
        name: str,
        max_hops: int,
    ) -> list[GraphRelationship]: ...


class GraphEnrichmentService:
    """Collect relationship evidence without inferring definite impact."""

    def __init__(
        self,
        repository: EnrichmentRepository,
        max_allowed_hops: int = MAX_GRAPH_TRAVERSAL_DEPTH,
    ) -> None:
        self._repository = repository
        self._max_allowed_hops = max_allowed_hops

    def enrich_entities(
        self,
        entity_names: list[str],
        max_hops: int = DEFAULT_GRAPH_TRAVERSAL_DEPTH,
    ) -> GraphContext:
        if not 1 <= max_hops <= self._max_allowed_hops:
            raise ValueError(
                f"max_hops must be between 1 and {self._max_allowed_hops}"
            )

        seed_entities: list[GraphEntity] = []
        relationships: dict[tuple[str, str, str], GraphRelationship] = {}
        seen_names: set[str] = set()

        for name in entity_names:
            normalized_name = name.casefold()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            entity = self._repository.get_entity(name)
            if entity is None:
                continue
            seed_entities.append(entity)
            for relationship in self._repository.get_related_relationships(
                entity.name,
                max_hops,
            ):
                relationships[relationship.identity] = relationship

        seed_name_keys = {entity.name.casefold() for entity in seed_entities}
        related_entities: dict[str, GraphEntity] = {}
        for relationship in relationships.values():
            if relationship.source_name.casefold() not in seed_name_keys:
                related_entities[relationship.source_name] = GraphEntity(
                    relationship.source_name,
                    relationship.source_type,
                )
            if relationship.target_name.casefold() not in seed_name_keys:
                related_entities[relationship.target_name] = GraphEntity(
                    relationship.target_name,
                    relationship.target_type,
                )

        return GraphContext(
            seed_entities=seed_entities,
            related_entities=[
                related_entities[name] for name in sorted(related_entities)
            ],
            relationships=sorted(
                relationships.values(),
                key=lambda item: item.identity,
            ),
            traversal_depth=max_hops,
        )
