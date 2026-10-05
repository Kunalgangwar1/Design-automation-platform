"""Parameterized graph queries and bounded neighborhood traversal."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from design_automation.core import MAX_GRAPH_TRAVERSAL_DEPTH
from design_automation.models import GraphEntity, GraphRelationship


class QueryClient(Protocol):
    def execute_read(
        self,
        query: str,
        parameters: Mapping[str, object] | None = None,
    ) -> list[dict[str, Any]]: ...


ENTITY_BY_NAME_QUERY = """
MATCH (entity:Entity {name: $name})
RETURN entity.name AS name,
       entity.entity_type AS entity_type,
       entity.description AS description,
       entity.owner AS owner
LIMIT 1
"""

DIRECT_RELATIONSHIPS_QUERY = """
MATCH (entity:Entity {name: $name})-[relationship]-(neighbor:Entity)
WITH startNode(relationship) AS source,
     relationship,
     endNode(relationship) AS target
RETURN source.name AS source_name,
       source.entity_type AS source_type,
       type(relationship) AS relationship_type,
       target.name AS target_name,
       target.entity_type AS target_type
ORDER BY source_name, relationship_type, target_name
"""

OUTGOING_RELATIONSHIPS_QUERY = """
MATCH (source:Entity {name: $name})-[relationship]->(target:Entity)
RETURN source.name AS source_name,
       source.entity_type AS source_type,
       type(relationship) AS relationship_type,
       target.name AS target_name,
       target.entity_type AS target_type
ORDER BY relationship_type, target_name
"""

INCOMING_RELATIONSHIPS_QUERY = """
MATCH (source:Entity)-[relationship]->(target:Entity {name: $name})
RETURN source.name AS source_name,
       source.entity_type AS source_type,
       type(relationship) AS relationship_type,
       target.name AS target_name,
       target.entity_type AS target_type
ORDER BY source_name, relationship_type
"""


def graph_entity_from_row(row: Mapping[str, Any]) -> GraphEntity:
    """Convert query data without exposing a Neo4j Record."""

    return GraphEntity(
        name=str(row["name"]),
        entity_type=str(row["entity_type"]),
        description=str(row.get("description") or ""),
        owner=str(row["owner"]) if row.get("owner") is not None else None,
    )


def graph_relationship_from_row(row: Mapping[str, Any]) -> GraphRelationship:
    """Convert a relationship row into the application model."""

    return GraphRelationship(
        source_name=str(row["source_name"]),
        source_type=str(row["source_type"]),
        relationship_type=str(row["relationship_type"]),
        target_name=str(row["target_name"]),
        target_type=str(row["target_type"]),
    )


class GraphRepository:
    """Query known entities and traverse their relationships safely."""

    def __init__(
        self,
        client: QueryClient,
        max_traversal_depth: int = MAX_GRAPH_TRAVERSAL_DEPTH,
    ) -> None:
        if max_traversal_depth <= 0:
            raise ValueError("max_traversal_depth must be positive")
        self._client = client
        self._max_traversal_depth = max_traversal_depth

    def get_entity(self, name: str) -> GraphEntity | None:
        rows = self._client.execute_read(ENTITY_BY_NAME_QUERY, {"name": name})
        return graph_entity_from_row(rows[0]) if rows else None

    def get_direct_relationships(self, name: str) -> list[GraphRelationship]:
        return self._relationships(DIRECT_RELATIONSHIPS_QUERY, name)

    def get_outgoing_relationships(self, name: str) -> list[GraphRelationship]:
        return self._relationships(OUTGOING_RELATIONSHIPS_QUERY, name)

    def get_incoming_relationships(self, name: str) -> list[GraphRelationship]:
        return self._relationships(INCOMING_RELATIONSHIPS_QUERY, name)

    def get_direct_neighbors(self, name: str) -> list[GraphEntity]:
        return _related_entities(name, self.get_direct_relationships(name))

    def get_related_relationships(
        self,
        name: str,
        max_hops: int,
    ) -> list[GraphRelationship]:
        """Traverse with repeated parameterized one-hop queries."""

        self._validate_depth(max_hops)
        frontier = {name}
        visited = {name}
        relationships: dict[tuple[str, str, str], GraphRelationship] = {}

        for _ in range(max_hops):
            next_frontier: set[str] = set()
            for entity_name in sorted(frontier):
                for relationship in self.get_direct_relationships(entity_name):
                    relationships[relationship.identity] = relationship
                    next_frontier.add(relationship.source_name)
                    next_frontier.add(relationship.target_name)
            next_frontier -= visited
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break

        return sorted(relationships.values(), key=lambda item: item.identity)

    def get_related_entities(self, name: str, max_hops: int) -> list[GraphEntity]:
        relationships = self.get_related_relationships(name, max_hops)
        return _related_entities(name, relationships)

    def _relationships(self, query: str, name: str) -> list[GraphRelationship]:
        rows = self._client.execute_read(query, {"name": name})
        return [graph_relationship_from_row(row) for row in rows]

    def _validate_depth(self, max_hops: int) -> None:
        if not 1 <= max_hops <= self._max_traversal_depth:
            raise ValueError(
                f"max_hops must be between 1 and {self._max_traversal_depth}"
            )


def _related_entities(
    seed_name: str,
    relationships: list[GraphRelationship],
) -> list[GraphEntity]:
    entities: dict[str, GraphEntity] = {}
    for relationship in relationships:
        if relationship.source_name != seed_name:
            entities[relationship.source_name] = GraphEntity(
                relationship.source_name,
                relationship.source_type,
            )
        if relationship.target_name != seed_name:
            entities[relationship.target_name] = GraphEntity(
                relationship.target_name,
                relationship.target_type,
            )
    return [entities[name] for name in sorted(entities)]
