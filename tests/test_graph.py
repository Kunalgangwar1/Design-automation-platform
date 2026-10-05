"""Unit tests for graph models, queries, matching, seeding, and enrichment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from design_automation.graph.client import GraphConfigurationError, Neo4jClient
from design_automation.graph.enrichment import GraphEnrichmentService
from design_automation.graph.matcher import (
    KNOWN_ENTITY_NAMES,
    detect_known_entities,
    find_known_entity_matches,
)
from design_automation.graph.repository import (
    DIRECT_RELATIONSHIPS_QUERY,
    ENTITY_BY_NAME_QUERY,
    GraphRepository,
    graph_relationship_from_row,
)
from design_automation.graph.seed import GraphSeeder
from design_automation.models import (
    GraphContext,
    GraphEntity,
    GraphRelationship,
    RetrievalResult,
)


def _relationship_row(
    source: str,
    relationship_type: str,
    target: str,
    source_type: str = "Service",
    target_type: str = "Service",
) -> dict[str, str]:
    return {
        "source_name": source,
        "source_type": source_type,
        "relationship_type": relationship_type,
        "target_name": target,
        "target_type": target_type,
    }


def _retrieval_result(section: str, text: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=f"chunk-{section}",
        document_id="doc-1",
        section=section,
        source="architecture/oms.md",
        text=text,
        similarity_score=0.8,
    )


def test_graph_application_models_are_small_structured_values() -> None:
    entity = GraphEntity("Order Service", "Service", "Owns order state", "Commerce")
    relationship = GraphRelationship(
        "Order Service",
        "Service",
        "CALLS",
        "Payment Service",
        "Service",
    )
    context = GraphContext([entity], [], [relationship], 1)

    assert relationship.identity == (
        "Order Service",
        "CALLS",
        "Payment Service",
    )
    assert context.seed_entities == [entity]


def test_graph_relationship_row_conversion_hides_driver_records() -> None:
    relationship = graph_relationship_from_row(
        _relationship_row("Order Service", "CALLS", "Payment Service")
    )
    assert relationship == GraphRelationship(
        "Order Service",
        "Service",
        "CALLS",
        "Payment Service",
        "Service",
    )


class FakeRecord:
    def data(self) -> dict[str, str]:
        return {"value": "ok"}


class FakeTransaction:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, query: str, parameters: dict[str, object]):
        self.calls.append((query, parameters))
        return [FakeRecord()]


class FakeSession:
    def __init__(self, transaction: FakeTransaction) -> None:
        self.transaction = transaction

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute_read(self, callback):
        return callback(self.transaction)

    def execute_write(self, callback):
        return callback(self.transaction)


class FakeDriver:
    def __init__(self) -> None:
        self.transaction = FakeTransaction()
        self.verified = False
        self.closed = False

    def session(self) -> FakeSession:
        return FakeSession(self.transaction)

    def verify_connectivity(self) -> None:
        self.verified = True

    def close(self) -> None:
        self.closed = True


def test_neo4j_client_verifies_uses_sessions_and_closes_driver() -> None:
    driver = FakeDriver()

    with Neo4jClient("bolt://example", "user", "password", driver=driver) as client:
        client.verify_connectivity()
        rows = client.execute_read("RETURN $value", {"value": "ok"})

    assert rows == [{"value": "ok"}]
    assert driver.transaction.calls == [("RETURN $value", {"value": "ok"})]
    assert driver.verified
    assert driver.closed


def test_neo4j_client_rejects_incomplete_configuration() -> None:
    with pytest.raises(GraphConfigurationError, match="must all be configured"):
        Neo4jClient("", "", "")


def test_known_entity_detection_scans_sections_and_text_and_deduplicates() -> None:
    results = [
        _retrieval_result(
            "Order Service",
            "Order Service calls Payment Service during a refund.",
        ),
        _retrieval_result(
            "Payment flow",
            "Payment Service calls the Payment Gateway.",
        ),
    ]

    detected = detect_known_entities(results)

    assert detected == ["Order Service", "Payment Service", "Payment Gateway"]


def test_partial_refund_evidence_detects_multiple_canonical_entities() -> None:
    results = [
        _retrieval_result(
            "Partial refund orchestration",
            (
                "The order service calls PAYMENT SERVICE, which sends the refund "
                "to payment gateway."
            ),
        ),
        _retrieval_result(
            "ORDERITEMSREFUNDED delivery",
            (
                "Inventory Service and notification service consume "
                "OrderItemsRefunded after the Order Service completes the refund."
            ),
        ),
    ]

    detected = detect_known_entities(results)

    assert detected == [
        "Order Service",
        "Payment Service",
        "Payment Gateway",
        "OrderItemsRefunded",
        "Inventory Service",
        "Notification Service",
    ]


def test_known_entity_detection_uses_the_complete_graph_catalog() -> None:
    results = [
        _retrieval_result(entity_name.lower(), "Supporting retrieved evidence.")
        for entity_name in KNOWN_ENTITY_NAMES
    ]

    assert detect_known_entities(results) == list(KNOWN_ENTITY_NAMES)


def test_entity_match_debugging_identifies_chunk_field_and_entity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = [
        _retrieval_result("Payment Service", "Calls the Payment Gateway."),
        _retrieval_result(
            "Downstream consumers",
            "Inventory Service and Notification Service consume OrderItemsRefunded.",
        ),
    ]

    matches = find_known_entity_matches(results)
    detected = detect_known_entities(results, debug=True)

    assert [
        (match.chunk_id, match.field, match.entity_name) for match in matches
    ] == [
        ("chunk-Payment Service", "section", "Payment Service"),
        ("chunk-Payment Service", "text", "Payment Gateway"),
        ("chunk-Downstream consumers", "text", "Inventory Service"),
        ("chunk-Downstream consumers", "text", "Notification Service"),
        ("chunk-Downstream consumers", "text", "OrderItemsRefunded"),
    ]
    assert detected == [
        "Payment Service",
        "Payment Gateway",
        "Inventory Service",
        "Notification Service",
        "OrderItemsRefunded",
    ]
    output = capsys.readouterr().out
    assert "chunk=chunk-Payment Service field=section entity=Payment Service" in output
    assert (
        "chunk=chunk-Downstream consumers field=text entity=OrderItemsRefunded"
        in output
    )


@dataclass
class RecordingQueryClient:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def execute_read(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        values = dict(parameters or {})
        self.calls.append((query, values))
        name = str(values.get("name", ""))
        if query == ENTITY_BY_NAME_QUERY and name == "Order Service":
            return [
                {
                    "name": "Order Service",
                    "entity_type": "Service",
                    "description": "Owns order state",
                    "owner": "Commerce",
                }
            ]
        if query == DIRECT_RELATIONSHIPS_QUERY:
            rows = {
                "Order Service": [
                    _relationship_row("Order Service", "CALLS", "Payment Service"),
                ],
                "Payment Service": [
                    _relationship_row("Order Service", "CALLS", "Payment Service"),
                    _relationship_row(
                        "Payment Service",
                        "CALLS",
                        "Payment Gateway",
                        target_type="ExternalSystem",
                    ),
                ],
            }
            return rows.get(name, [])
        return []


def test_repository_passes_entity_name_as_a_cypher_parameter() -> None:
    client = RecordingQueryClient()
    repository = GraphRepository(client)
    suspicious_name = "Order Service' MATCH (all)"

    repository.get_entity(suspicious_name)

    query, parameters = client.calls[0]
    assert "$name" in query
    assert suspicious_name not in query
    assert parameters == {"name": suspicious_name}


def test_repository_supports_bounded_multi_hop_traversal() -> None:
    client = RecordingQueryClient()
    repository = GraphRepository(client, max_traversal_depth=2)

    one_hop = repository.get_related_relationships("Order Service", 1)
    two_hops = repository.get_related_relationships("Order Service", 2)

    assert len(one_hop) == 1
    assert len(two_hops) == 2
    assert two_hops[-1].target_name == "Payment Gateway"
    with pytest.raises(ValueError, match="between 1 and 2"):
        repository.get_related_relationships("Order Service", 3)


@dataclass
class StubEnrichmentRepository:
    calls: list[tuple[str, int]] = field(default_factory=list)

    def get_entity(self, name: str) -> GraphEntity | None:
        if name == "Missing Service":
            return None
        return GraphEntity(name, "Service")

    def get_related_relationships(
        self,
        name: str,
        max_hops: int,
    ) -> list[GraphRelationship]:
        self.calls.append((name, max_hops))
        shared = GraphRelationship(
            "Order Service",
            "Service",
            "CALLS",
            "Payment Service",
            "Service",
        )
        return [shared, shared]


def test_graph_enrichment_deduplicates_seeds_and_relationships() -> None:
    repository = StubEnrichmentRepository()
    service = GraphEnrichmentService(repository)

    context = service.enrich_entities(
        ["Order Service", "Order Service", "Payment Service"],
        max_hops=2,
    )

    assert [entity.name for entity in context.seed_entities] == [
        "Order Service",
        "Payment Service",
    ]
    assert len(context.relationships) == 1
    assert context.related_entities == []
    assert repository.calls == [("Order Service", 2), ("Payment Service", 2)]
    assert context.traversal_depth == 2


def test_graph_enrichment_handles_nonexistent_entity() -> None:
    context = GraphEnrichmentService(StubEnrichmentRepository()).enrich_entities(
        ["Missing Service"],
        max_hops=1,
    )

    assert context.seed_entities == []
    assert context.relationships == []
    assert context.related_entities == []


@dataclass
class RecordingSeedClient:
    writes: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def execute_write(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        self.writes.append((query, dict(parameters or {})))
        return []

    def execute_read(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        return [{"node_count": 12, "relationship_count": 20}]


def test_graph_seeding_uses_constraints_merge_and_parameter_payloads() -> None:
    client = RecordingSeedClient()

    first = GraphSeeder(client).seed()
    second = GraphSeeder(client).seed()

    assert first == second
    assert first.node_count == 12
    assert first.relationship_count == 20
    assert "CREATE CONSTRAINT" in client.writes[0][0]
    assert all(
        "MERGE" in query
        for query, _ in client.writes
        if "CREATE CONSTRAINT" not in query
    )
    assert any("nodes" in parameters for _, parameters in client.writes)
    assert any("relationships" in parameters for _, parameters in client.writes)
