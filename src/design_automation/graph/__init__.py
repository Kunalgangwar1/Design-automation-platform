"""Neo4j knowledge-graph construction, queries, and RAG enrichment."""

from design_automation.graph.client import GraphConfigurationError, Neo4jClient
from design_automation.graph.enrichment import GraphEnrichmentService
from design_automation.graph.matcher import KNOWN_ENTITY_NAMES, detect_known_entities
from design_automation.graph.repository import GraphRepository

__all__ = [
    "GraphConfigurationError",
    "GraphEnrichmentService",
    "GraphRepository",
    "KNOWN_ENTITY_NAMES",
    "Neo4jClient",
    "detect_known_entities",
]
