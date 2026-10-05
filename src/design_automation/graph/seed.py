"""Idempotently seed the fictional OMS graph."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from design_automation.graph.client import GraphConfigurationError, Neo4jClient
from design_automation.graph.schema import (
    ENTITY_NAME_CONSTRAINT,
    GRAPH_COUNTS_QUERY,
    NODE_MERGE_QUERIES,
    OMS_ENTITIES,
    OMS_RELATIONSHIPS,
    RELATIONSHIP_MERGE_QUERIES,
)


class SeedClient(Protocol):
    def execute_read(
        self,
        query: str,
        parameters: Mapping[str, object] | None = None,
    ) -> list[dict[str, Any]]: ...

    def execute_write(
        self,
        query: str,
        parameters: Mapping[str, object] | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class SeedSummary:
    node_count: int
    relationship_count: int


class GraphSeeder:
    """Apply constraints and merge all fixed seed entities and relationships."""

    def __init__(self, client: SeedClient) -> None:
        self._client = client

    def seed(self) -> SeedSummary:
        self._client.execute_write(ENTITY_NAME_CONSTRAINT)

        nodes_by_label: dict[str, list[dict[str, object]]] = defaultdict(list)
        for entity in OMS_ENTITIES:
            nodes_by_label[entity.entity_type].append(asdict(entity))
        for label, nodes in nodes_by_label.items():
            self._client.execute_write(NODE_MERGE_QUERIES[label], {"nodes": nodes})

        relationships_by_type: dict[str, list[dict[str, object]]] = defaultdict(list)
        for relationship in OMS_RELATIONSHIPS:
            relationships_by_type[relationship.relationship_type].append(
                asdict(relationship)
            )
        for relationship_type, relationships in relationships_by_type.items():
            self._client.execute_write(
                RELATIONSHIP_MERGE_QUERIES[relationship_type],
                {"relationships": relationships},
            )

        rows = self._client.execute_read(GRAPH_COUNTS_QUERY)
        counts = rows[0] if rows else {}
        return SeedSummary(
            node_count=int(counts.get("node_count", 0)),
            relationship_count=int(counts.get("relationship_count", 0)),
        )


def main() -> None:
    """Seed the configured local Neo4j instance and print its final counts."""

    try:
        with Neo4jClient.from_environment() as client:
            client.verify_connectivity()
            summary = GraphSeeder(client).seed()
    except GraphConfigurationError as exc:
        raise SystemExit(f"Neo4j configuration error: {exc}") from exc

    print("Neo4j graph seed complete")
    print(f"Nodes: {summary.node_count}")
    print(f"Relationships: {summary.relationship_count}")


if __name__ == "__main__":
    main()
