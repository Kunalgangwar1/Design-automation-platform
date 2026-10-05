"""Inspect an entity or enrich a hybrid retrieval query with graph evidence."""

import argparse
from pathlib import Path

from neo4j.exceptions import Neo4jError, ServiceUnavailable

from design_automation.core import (
    DEFAULT_DENSE_CANDIDATE_COUNT,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_GRAPH_TRAVERSAL_DEPTH,
    DEFAULT_LEXICAL_CANDIDATE_COUNT,
    DEFAULT_RRF_CONSTANT,
    DEFAULT_TOP_K,
    FAISS_INDEX_PATH,
    FAISS_MAPPING_PATH,
)
from design_automation.graph.client import GraphConfigurationError, Neo4jClient
from design_automation.graph.enrichment import GraphEnrichmentService
from design_automation.graph.matcher import detect_known_entities
from design_automation.graph.repository import GraphRepository
from design_automation.ingestion import ingest_documents
from design_automation.models import GraphContext, RetrievalMode, RetrievalResponse
from design_automation.retrieval.coordinator import RetrievalConfig, RetrievalCoordinator
from design_automation.retrieval.embeddings import SentenceTransformerEmbeddingService
from design_automation.retrieval.lexical import BM25Retriever
from design_automation.retrieval.retriever import SemanticRetriever
from design_automation.retrieval.vector_store import FaissVectorStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect bounded OMS graph relationships with optional RAG seeds."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--query", help="Run hybrid retrieval, then graph enrichment")
    target.add_argument("--entity", help="Inspect one known graph entity directly")
    parser.add_argument("--hops", type=int, default=DEFAULT_GRAPH_TRAVERSAL_DEPTH)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--dense-candidates",
        type=int,
        default=DEFAULT_DENSE_CANDIDATE_COUNT,
    )
    parser.add_argument(
        "--lexical-candidates",
        type=int,
        default=DEFAULT_LEXICAL_CANDIDATE_COUNT,
    )
    parser.add_argument("--rrf-constant", type=int, default=DEFAULT_RRF_CONSTANT)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--model-name", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--device", default=DEFAULT_EMBEDDING_DEVICE)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_EMBEDDING_BATCH_SIZE)
    parser.add_argument("--index-path", type=Path, default=FAISS_INDEX_PATH)
    parser.add_argument("--mapping-path", type=Path, default=FAISS_MAPPING_PATH)
    return parser


def _hybrid_retrieve(args: argparse.Namespace) -> RetrievalResponse:
    index_exists = args.index_path.exists()
    mapping_exists = args.mapping_path.exists()
    should_build = args.rebuild or not (index_exists and mapping_exists)

    if should_build:
        embedding_service = SentenceTransformerEmbeddingService(
            args.model_name,
            args.device,
        )
        chunks = ingest_documents().chunks
        dense_retriever = SemanticRetriever.build(
            chunks,
            embedding_service,
            args.batch_size,
        )
        store = dense_retriever.vector_store
        store.save(args.index_path, args.mapping_path, embedding_service.model_name)
    else:
        store, stored_model = FaissVectorStore.load(
            args.index_path,
            args.mapping_path,
        )
        if stored_model != args.model_name:
            raise ValueError(
                "Persisted embedding model differs from --model-name; use --rebuild"
            )
        embedding_service = SentenceTransformerEmbeddingService(
            stored_model,
            args.device,
        )
        dense_retriever = SemanticRetriever(embedding_service, store)
        chunks = list(store.chunks)

    coordinator = RetrievalCoordinator(
        dense_retriever,
        BM25Retriever(chunks),
        RetrievalConfig(
            rrf_constant=args.rrf_constant,
            dense_candidate_count=args.dense_candidates,
            lexical_candidate_count=args.lexical_candidates,
        ),
    )
    return coordinator.retrieve(args.query, RetrievalMode.HYBRID, args.top_k)


def _print_context(context: GraphContext) -> None:
    print(f"Traversal depth: {context.traversal_depth}")
    print(
        "Seed entities: "
        + (", ".join(entity.name for entity in context.seed_entities) or "<none>")
    )
    print(
        "Related entities: "
        + (", ".join(entity.name for entity in context.related_entities) or "<none>")
    )
    print("Graph relationships:")
    if not context.relationships:
        print("  <none>")
    for relationship in context.relationships:
        print(
            f"  ({relationship.source_name}:{relationship.source_type})"
            f"-[:{relationship.relationship_type}]->"
            f"({relationship.target_name}:{relationship.target_type})"
        )
    print(
        "Note: adjacency is relationship evidence for impact analysis, not proof "
        "that every related entity requires modification."
    )


def main() -> None:
    args = _parser().parse_args()

    try:
        with Neo4jClient.from_environment() as client:
            client.verify_connectivity()
            repository = GraphRepository(client)
            enrichment = GraphEnrichmentService(repository)

            if args.entity:
                print(f"Entity: {args.entity}")
                context = enrichment.enrich_entities([args.entity], args.hops)
            else:
                print(f"Query: {args.query}")
                response = _hybrid_retrieve(args)
                print("Hybrid retrieved sections:")
                for rank, result in enumerate(response.results, start=1):
                    print(f"  {rank}. {result.section or '<none>'} [{result.chunk_id}]")
                detected_entities = detect_known_entities(response.results)
                print(
                    "Detected entities: "
                    + (", ".join(detected_entities) or "<none>")
                )
                context = enrichment.enrich_entities(detected_entities, args.hops)
            _print_context(context)
    except GraphConfigurationError as exc:
        raise SystemExit(f"Neo4j configuration error: {exc}") from exc
    except (Neo4jError, ServiceUnavailable) as exc:
        raise SystemExit(f"Neo4j query failed: {exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"Graph inspection error: {exc}") from exc


if __name__ == "__main__":
    main()
