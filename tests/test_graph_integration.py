"""Optional local-Neo4j integration smoke test."""

import os

import pytest

from design_automation.graph.client import Neo4jClient
from design_automation.graph.repository import GraphRepository


RUN_INTEGRATION = os.getenv("RUN_NEO4J_INTEGRATION") == "1"
HAS_CONFIGURATION = all(
    os.getenv(name) for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
)


@pytest.mark.integration
@pytest.mark.skipif(
    not (RUN_INTEGRATION and HAS_CONFIGURATION),
    reason="requires RUN_NEO4J_INTEGRATION=1 and Neo4j environment variables",
)
def test_seeded_order_service_is_queryable() -> None:
    with Neo4jClient.from_environment() as client:
        client.verify_connectivity()
        entity = GraphRepository(client).get_entity("Order Service")

    assert entity is not None
    assert entity.entity_type == "Service"
