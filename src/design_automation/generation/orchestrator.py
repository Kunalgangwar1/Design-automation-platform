"""Application-level orchestration for preparing a generation request."""

import logging
from collections.abc import Callable
from time import perf_counter
from typing import Protocol

from neo4j.exceptions import DriverError, Neo4jError

from design_automation.core import (
    DEFAULT_GRAPH_TRAVERSAL_DEPTH,
    DEFAULT_TOP_K,
    GRAPH_DEGRADATION_ENABLED,
)
from design_automation.core.observability import elapsed_ms, log_event
from design_automation.generation.context import ContextBuilder
from design_automation.generation.prompts import PromptBuilder
from design_automation.graph.matcher import detect_known_entities
from design_automation.models import (
    GenerationRequest,
    GenerationRequestStatus,
    GraphContext,
    PreparedGenerationRequest,
    PromptTemplate,
    RetrievalMode,
    RetrievalResponse,
    RetrievalResult,
)


logger = logging.getLogger(__name__)
GRAPH_UNAVAILABLE_WARNING = (
    "Graph enrichment was unavailable; generation used document evidence only."
)


class HybridRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        mode: RetrievalMode | str,
        top_k: int,
    ) -> RetrievalResponse: ...


class GraphEnricher(Protocol):
    def enrich_entities(
        self,
        entity_names: list[str],
        max_hops: int,
    ) -> GraphContext: ...


class PromptTemplates(Protocol):
    def find_active_by_name(self, name: str) -> PromptTemplate | None: ...


class GenerationRequests(Protocol):
    def create(
        self,
        requirement_text: str,
        requested_section: str,
        prompt_template_id: int,
    ) -> GenerationRequest: ...

    def update_status(
        self,
        request_id: str,
        status: GenerationRequestStatus,
    ) -> GenerationRequest: ...


class PromptTemplateNotFoundError(LookupError):
    """Raised when no active template exists for the requested section."""


class GenerationOrchestrator:
    """Coordinate existing services and stop once grounded context is ready."""

    def __init__(
        self,
        retriever: HybridRetriever,
        graph_enricher: GraphEnricher,
        template_repository: PromptTemplates,
        request_repository: GenerationRequests,
        context_builder: ContextBuilder | None = None,
        prompt_builder: PromptBuilder | None = None,
        entity_detector: Callable[[list[RetrievalResult]], list[str]] = (
            detect_known_entities
        ),
        allow_graph_degradation: bool = GRAPH_DEGRADATION_ENABLED,
    ) -> None:
        self._retriever = retriever
        self._graph_enricher = graph_enricher
        self._templates = template_repository
        self._requests = request_repository
        self._context_builder = context_builder or ContextBuilder()
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._entity_detector = entity_detector
        self._allow_graph_degradation = allow_graph_degradation

    def prepare_generation_context(
        self,
        requirement: str,
        requested_section: str,
        top_k: int = DEFAULT_TOP_K,
        max_hops: int = DEFAULT_GRAPH_TRAVERSAL_DEPTH,
    ) -> PreparedGenerationRequest:
        persistence_started = perf_counter()
        template = self._templates.find_active_by_name(requested_section)
        if template is None or template.id is None:
            raise PromptTemplateNotFoundError(
                f"No active prompt template for section: {requested_section}"
            )

        request = self._requests.create(
            requirement,
            requested_section,
            template.id,
        )
        log_event(
            logger,
            logging.INFO,
            "generation_request_created",
            generation_request_id=request.id,
            requested_section=requested_section,
            stage="persistence",
            latency_ms=elapsed_ms(persistence_started),
        )
        operational_warnings: list[str] = []
        try:
            retrieval_started = perf_counter()
            retrieval = self._retriever.retrieve(
                requirement,
                RetrievalMode.HYBRID,
                top_k,
            )
            log_event(
                logger,
                logging.INFO,
                "generation_stage_completed",
                generation_request_id=request.id,
                stage="retrieval",
                status="success",
                latency_ms=elapsed_ms(retrieval_started),
                retrieved_chunk_count=len(retrieval.results),
            )
            detected_entities = self._entity_detector(retrieval.results)
            graph_started = perf_counter()
            try:
                graph_context = self._graph_enricher.enrich_entities(
                    detected_entities,
                    max_hops,
                )
                log_event(
                    logger,
                    logging.INFO,
                    "generation_stage_completed",
                    generation_request_id=request.id,
                    stage="graph_enrichment",
                    status="success",
                    latency_ms=elapsed_ms(graph_started),
                    detected_entity_count=len(detected_entities),
                    graph_relationship_count=len(graph_context.relationships),
                )
            except (Neo4jError, DriverError) as exc:
                if not self._allow_graph_degradation:
                    raise
                graph_context = GraphContext(traversal_depth=max_hops)
                operational_warnings.append(GRAPH_UNAVAILABLE_WARNING)
                log_event(
                    logger,
                    logging.WARNING,
                    "graph_enrichment_degraded",
                    generation_request_id=request.id,
                    stage="graph_enrichment",
                    status="degraded",
                    latency_ms=elapsed_ms(graph_started),
                    detected_entity_count=len(detected_entities),
                    graph_relationship_count=0,
                    error_type=type(exc).__name__,
                )

            context_started = perf_counter()
            context = self._context_builder.build(
                requirement,
                requested_section,
                retrieval.results,
                graph_context,
            )
            rendered_prompt = self._prompt_builder.build(template, context)
            log_event(
                logger,
                logging.INFO,
                "generation_stage_completed",
                generation_request_id=request.id,
                stage="context_prompt_preparation",
                status="success",
                latency_ms=elapsed_ms(context_started),
                document_evidence_count=len(context.document_evidence),
                graph_evidence_count=len(context.graph_evidence),
            )
            persistence_started = perf_counter()
            request = self._requests.update_status(
                request.id,
                GenerationRequestStatus.CONTEXT_READY,
            )
            log_event(
                logger,
                logging.INFO,
                "generation_stage_completed",
                generation_request_id=request.id,
                stage="persistence",
                status="success",
                latency_ms=elapsed_ms(persistence_started),
                request_status=request.status.value,
            )
        except Exception as exc:
            failure_started = perf_counter()
            self._requests.update_status(
                request.id,
                GenerationRequestStatus.FAILED,
            )
            log_event(
                logger,
                logging.ERROR,
                "generation_preparation_failed",
                generation_request_id=request.id,
                status="failed",
                persistence_latency_ms=elapsed_ms(failure_started),
                error_type=type(exc).__name__,
            )
            raise

        return PreparedGenerationRequest(
            request_id=request.id,
            requested_section=requested_section,
            prompt_name=template.name,
            prompt_version=template.version,
            context=context,
            rendered_prompt=rendered_prompt,
            detected_entities=detected_entities,
            status=request.status,
            operational_warnings=operational_warnings,
        )
