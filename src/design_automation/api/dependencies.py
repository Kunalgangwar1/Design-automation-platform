"""Shared resource lifecycle and per-request application dependencies."""

import hashlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

from fastapi import Request
from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from design_automation.core import (
    DEFAULT_CONTEXT_DOCUMENT_LIMIT,
    DEFAULT_DENSE_CANDIDATE_COUNT,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LEXICAL_CANDIDATE_COUNT,
    DEFAULT_RRF_CONSTANT,
    FAISS_INDEX_PATH,
    FAISS_MAPPING_PATH,
    GRAPH_DEGRADATION_ENABLED,
    RETRIEVAL_CACHE_ENABLED,
    RETRIEVAL_CACHE_MAX_ENTRIES,
    RETRIEVAL_CACHE_TTL_SECONDS,
)
from design_automation.core.observability import log_event
from design_automation.database import (
    GenerationRequestRepository,
    GenerationRunRepository,
    PromptTemplateRepository,
    build_engine,
    build_session_factory,
)
from design_automation.generation import (
    ContextBuilder,
    GenerationApplicationService,
    GenerationOrchestrator,
    GenerationService,
    LLMClient,
    build_llm_clients,
)
from design_automation.graph.client import Neo4jClient
from design_automation.graph.enrichment import GraphEnrichmentService
from design_automation.graph.repository import GraphRepository
from design_automation.ingestion import ingest_documents
from design_automation.retrieval.coordinator import RetrievalConfig, RetrievalCoordinator
from design_automation.retrieval.cache import (
    CachedRetrievalCoordinator,
    RetrievalCache,
)
from design_automation.retrieval.embeddings import SentenceTransformerEmbeddingService
from design_automation.retrieval.lexical import BM25Retriever
from design_automation.retrieval.retriever import SemanticRetriever
from design_automation.retrieval.vector_store import FaissVectorStore


logger = logging.getLogger(__name__)


@dataclass
class ApplicationResources:
    """Process-wide resources initialized once by FastAPI lifespan."""

    engine: Engine
    session_factory: sessionmaker[Session]
    retrieval: CachedRetrievalCoordinator
    graph_enrichment: GraphEnrichmentService
    llm_clients: list[LLMClient]
    neo4j_client: Neo4jClient
    faiss_index_size: int
    corpus_version: str

    def close(self) -> None:
        self.neo4j_client.close()
        self.engine.dispose()


def _mapping_version() -> str:
    digest = hashlib.sha256()
    with FAISS_MAPPING_PATH.open("rb") as mapping_file:
        for block in iter(lambda: mapping_file.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _build_retrieval() -> tuple[CachedRetrievalCoordinator, int, str]:
    if FAISS_INDEX_PATH.exists() and FAISS_MAPPING_PATH.exists():
        store, stored_model = FaissVectorStore.load(
            FAISS_INDEX_PATH,
            FAISS_MAPPING_PATH,
        )
        if stored_model != DEFAULT_EMBEDDING_MODEL:
            raise ValueError(
                "Persisted embedding model does not match application configuration"
            )
        embedding_service = SentenceTransformerEmbeddingService(
            stored_model,
            DEFAULT_EMBEDDING_DEVICE,
        )
        dense_retriever = SemanticRetriever(embedding_service, store)
        chunks = list(store.chunks)
    else:
        embedding_service = SentenceTransformerEmbeddingService(
            DEFAULT_EMBEDDING_MODEL,
            DEFAULT_EMBEDDING_DEVICE,
        )
        chunks = ingest_documents().chunks
        dense_retriever = SemanticRetriever.build(
            chunks,
            embedding_service,
            DEFAULT_EMBEDDING_BATCH_SIZE,
        )
        dense_retriever.vector_store.save(
            FAISS_INDEX_PATH,
            FAISS_MAPPING_PATH,
            embedding_service.model_name,
        )

    config = RetrievalConfig(
        rrf_constant=DEFAULT_RRF_CONSTANT,
        dense_candidate_count=DEFAULT_DENSE_CANDIDATE_COUNT,
        lexical_candidate_count=DEFAULT_LEXICAL_CANDIDATE_COUNT,
    )
    coordinator = RetrievalCoordinator(
        dense_retriever,
        BM25Retriever(chunks),
        config,
    )
    corpus_version = _mapping_version()
    settings_version = (
        f"model={DEFAULT_EMBEDDING_MODEL};rrf={config.rrf_constant};"
        f"dense={config.dense_candidate_count};"
        f"lexical={config.lexical_candidate_count}"
    )
    cache = (
        RetrievalCache(
            RETRIEVAL_CACHE_TTL_SECONDS,
            RETRIEVAL_CACHE_MAX_ENTRIES,
        )
        if RETRIEVAL_CACHE_ENABLED
        else None
    )
    return (
        CachedRetrievalCoordinator(
            coordinator,
            corpus_version,
            settings_version,
            cache,
        ),
        dense_retriever.vector_store.size,
        corpus_version,
    )


def build_application_resources() -> ApplicationResources:
    """Build expensive resources once before accepting HTTP requests."""

    engine = build_engine()
    neo4j_client: Neo4jClient | None = None
    try:
        session_factory = build_session_factory(engine)
        retrieval, faiss_index_size, corpus_version = _build_retrieval()
        neo4j_client = Neo4jClient.from_environment()
        try:
            neo4j_client.verify_connectivity()
        except (Neo4jError, DriverError) as exc:
            if not GRAPH_DEGRADATION_ENABLED:
                raise
            log_event(
                logger,
                logging.WARNING,
                "startup_graph_unavailable",
                status="degraded",
                error_type=type(exc).__name__,
            )
        graph_enrichment = GraphEnrichmentService(
            GraphRepository(neo4j_client)
        )
        return ApplicationResources(
            engine=engine,
            session_factory=session_factory,
            retrieval=retrieval,
            graph_enrichment=graph_enrichment,
            llm_clients=build_llm_clients(),
            neo4j_client=neo4j_client,
            faiss_index_size=faiss_index_size,
            corpus_version=corpus_version,
        )
    except Exception:
        if neo4j_client is not None:
            neo4j_client.close()
        engine.dispose()
        raise


@dataclass(frozen=True)
class ReadinessStatus:
    """Safe dependency state returned by the readiness boundary."""

    ready: bool
    checks: dict[str, bool]


def get_readiness_status(request: Request) -> ReadinessStatus:
    """Check loaded retrieval plus database and graph connectivity, never LLMs."""

    resources = cast(ApplicationResources | None, request.app.state.resources)
    if resources is None:
        return ReadinessStatus(
            ready=False,
            checks={"postgresql": False, "neo4j": False, "faiss": False},
        )

    checks = {"postgresql": False, "neo4j": False, "faiss": False}
    checks["faiss"] = resources.faiss_index_size > 0
    try:
        with resources.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["postgresql"] = True
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "readiness_check_failed",
            dependency="postgresql",
            error_type=type(exc).__name__,
        )
    try:
        resources.neo4j_client.verify_connectivity()
        checks["neo4j"] = True
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "readiness_check_failed",
            dependency="neo4j",
            error_type=type(exc).__name__,
        )
    return ReadinessStatus(ready=all(checks.values()), checks=checks)


def get_generation_application_service(
    request: Request,
) -> Iterator[GenerationApplicationService]:
    """Create repositories with one SQLAlchemy session for this HTTP request."""

    resources = cast(ApplicationResources | None, request.app.state.resources)
    if resources is None:
        raise RuntimeError("Application resources are not initialized")

    with resources.session_factory() as session:
        request_repository = GenerationRequestRepository(session)
        run_repository = GenerationRunRepository(session)
        orchestrator = GenerationOrchestrator(
            resources.retrieval,
            resources.graph_enrichment,
            PromptTemplateRepository(session),
            request_repository,
            ContextBuilder(DEFAULT_CONTEXT_DOCUMENT_LIMIT),
        )
        generation_service = GenerationService(
            resources.llm_clients,
            request_repository,
            run_repository,
        )
        yield GenerationApplicationService(
            orchestrator,
            generation_service,
            request_repository,
            run_repository,
        )
