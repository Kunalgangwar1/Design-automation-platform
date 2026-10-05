"""Fixed OMS graph seed data and idempotent Cypher statements."""

from design_automation.models import GraphEntity, GraphRelationship


OMS_ENTITIES = [
    GraphEntity(
        "Order Management System",
        "System",
        "Enterprise system responsible for the order lifecycle.",
        "Commerce Architecture",
    ),
    GraphEntity(
        "Support Portal",
        "System",
        "Customer-support user interface for order operations.",
        "Customer Operations",
    ),
    GraphEntity("Order Service", "Service", "Owns order state and refund eligibility."),
    GraphEntity("Payment Service", "Service", "Owns payment and refund execution."),
    GraphEntity("Inventory Service", "Service", "Owns stock adjustments."),
    GraphEntity("Notification Service", "Service", "Owns customer notifications."),
    GraphEntity("Order Database", "Database", "Stores order and refund audit state."),
    GraphEntity(
        "Payment Gateway",
        "ExternalSystem",
        "External provider that executes card refunds.",
    ),
    GraphEntity("Event Bus", "System", "Delivers asynchronous business events."),
    GraphEntity(
        "Partial Refund API",
        "Interface",
        "Support command exposed as POST /orders/{order_id}/refunds.",
    ),
    GraphEntity(
        "OrderItemsRefunded",
        "Event",
        "Versioned event published after confirmed refund success.",
    ),
    GraphEntity(
        "Partial Refund Process",
        "Process",
        "Cross-service process for an item-level completed-order refund.",
    ),
]


def _relationship(
    source: str,
    source_type: str,
    relationship_type: str,
    target: str,
    target_type: str,
) -> GraphRelationship:
    return GraphRelationship(source, source_type, relationship_type, target, target_type)


OMS_RELATIONSHIPS = [
    _relationship("Order Management System", "System", "CONTAINS", "Order Service", "Service"),
    _relationship("Order Management System", "System", "CONTAINS", "Payment Service", "Service"),
    _relationship("Order Management System", "System", "CONTAINS", "Inventory Service", "Service"),
    _relationship("Order Management System", "System", "CONTAINS", "Notification Service", "Service"),
    _relationship("Order Management System", "System", "CONTAINS", "Order Database", "Database"),
    _relationship("Order Management System", "System", "CONTAINS", "Event Bus", "System"),
    _relationship("Order Management System", "System", "CONTAINS", "Partial Refund API", "Interface"),
    _relationship("Support Portal", "System", "CALLS", "Order Service", "Service"),
    _relationship("Support Portal", "System", "CALLS", "Partial Refund API", "Interface"),
    _relationship("Order Service", "Service", "CALLS", "Payment Service", "Service"),
    _relationship("Payment Service", "Service", "CALLS", "Payment Gateway", "ExternalSystem"),
    _relationship("Order Service", "Service", "WRITES_TO", "Order Database", "Database"),
    _relationship("Order Service", "Service", "PUBLISHES", "OrderItemsRefunded", "Event"),
    _relationship("Inventory Service", "Service", "CONSUMES", "OrderItemsRefunded", "Event"),
    _relationship("Notification Service", "Service", "CONSUMES", "OrderItemsRefunded", "Event"),
    _relationship("OrderItemsRefunded", "Event", "DELIVERED_VIA", "Event Bus", "System"),
    _relationship("Order Service", "Service", "PARTICIPATES_IN", "Partial Refund Process", "Process"),
    _relationship("Payment Service", "Service", "PARTICIPATES_IN", "Partial Refund Process", "Process"),
    _relationship("Inventory Service", "Service", "PARTICIPATES_IN", "Partial Refund Process", "Process"),
    _relationship("Notification Service", "Service", "PARTICIPATES_IN", "Partial Refund Process", "Process"),
]


ENTITY_NAME_CONSTRAINT = """
CREATE CONSTRAINT entity_name_unique IF NOT EXISTS
FOR (entity:Entity) REQUIRE entity.name IS UNIQUE
"""

# Labels and relationship types come only from these fixed allowlists. User
# values remain in parameter payloads and are never interpolated into Cypher.
NODE_MERGE_QUERIES = {
    label: f"""
    UNWIND $nodes AS row
    MERGE (entity:Entity:{label} {{name: row.name}})
    SET entity.description = row.description,
        entity.owner = row.owner,
        entity.entity_type = row.entity_type
    """
    for label in (
        "System",
        "Service",
        "Database",
        "ExternalSystem",
        "Interface",
        "Event",
        "Process",
    )
}

RELATIONSHIP_MERGE_QUERIES = {
    relationship_type: f"""
    UNWIND $relationships AS row
    MATCH (source:Entity {{name: row.source_name}})
    MATCH (target:Entity {{name: row.target_name}})
    MERGE (source)-[:{relationship_type}]->(target)
    """
    for relationship_type in (
        "CONTAINS",
        "CALLS",
        "WRITES_TO",
        "PUBLISHES",
        "CONSUMES",
        "DELIVERED_VIA",
        "PARTICIPATES_IN",
    )
}

GRAPH_COUNTS_QUERY = """
MATCH (entity:Entity)
WITH count(entity) AS node_count
OPTIONAL MATCH (:Entity)-[relationship]->(:Entity)
RETURN node_count, count(relationship) AS relationship_count
"""
