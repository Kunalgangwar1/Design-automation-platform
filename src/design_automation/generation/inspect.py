"""Inspect Day-6 preparation and optional provider-fallback generation."""

import argparse
from pathlib import Path

from neo4j.exceptions import Neo4jError, ServiceUnavailable

from design_automation.core import (
    DEFAULT_CONTEXT_DOCUMENT_LIMIT,
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
from design_automation.database import (
    DatabaseConfigurationError,
    GenerationRequestRepository,
    GenerationRunRepository,
    PromptTemplateRepository,
    build_engine,
    build_session_factory,
)
from design_automation.generation import (
    ContextBuilder,
    GenerationError,
    GenerationOrchestrator,
    GenerationService,
    build_llm_clients,
)
from design_automation.graph.client import GraphConfigurationError, Neo4jClient
from design_automation.graph.enrichment import GraphEnrichmentService
from design_automation.graph.repository import GraphRepository
from design_automation.ingestion import ingest_documents
from design_automation.models import (
    GenerationResult,
    GenerationRun,
    PreparedGenerationRequest,
)
from design_automation.retrieval.coordinator import RetrievalConfig, RetrievalCoordinator
from design_automation.retrieval.embeddings import SentenceTransformerEmbeddingService
from design_automation.retrieval.lexical import BM25Retriever
from design_automation.retrieval.retriever import SemanticRetriever
from design_automation.retrieval.vector_store import FaissVectorStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare grounded context and optionally generate an SDD section."
    )
    parser.add_argument("--requirement", required=True)
    parser.add_argument("--section", default="impact_analysis")
    parser.add_argument("--hops", type=int, default=DEFAULT_GRAPH_TRAVERSAL_DEPTH)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--max-document-chunks",
        type=int,
        default=DEFAULT_CONTEXT_DOCUMENT_LIMIT,
    )
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
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate using the configured OpenAI-to-Gemini provider order",
    )
    return parser


def _retrieval_coordinator(args: argparse.Namespace) -> RetrievalCoordinator:
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
        dense_retriever.vector_store.save(
            args.index_path,
            args.mapping_path,
            embedding_service.model_name,
        )
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

    return RetrievalCoordinator(
        dense_retriever,
        BM25Retriever(chunks),
        RetrievalConfig(
            rrf_constant=args.rrf_constant,
            dense_candidate_count=args.dense_candidates,
            lexical_candidate_count=args.lexical_candidates,
        ),
    )


def _print_prepared(prepared: PreparedGenerationRequest) -> None:
    # Kept local to the CLI so application models remain presentation-agnostic.
    print(f"Request ID: {prepared.request_id}")
    print(f"Requested section: {prepared.requested_section}")
    print("Detected graph entities: " + (
        ", ".join(prepared.detected_entities) or "<none>"
    ))
    print(f"Document evidence count: {len(prepared.context.document_evidence)}")
    print("Hybrid retrieved chunks:")
    for evidence in prepared.context.document_evidence:
        print(
            f"  {evidence.retrieval_rank}. {evidence.document_title} / "
            f"{evidence.section or '<none>'} [{evidence.chunk_id}]"
        )
    print(f"Graph evidence count: {len(prepared.context.graph_evidence)}")
    print("Graph relationships:")
    for evidence in prepared.context.graph_evidence:
        print(
            f"  ({evidence.source_entity}:{evidence.source_type})"
            f"-[:{evidence.relationship}]->"
            f"({evidence.target_entity}:{evidence.target_type})"
        )
    if not prepared.context.graph_evidence:
        print("  <none>")
    print(f"Prompt: {prepared.prompt_name} v{prepared.prompt_version}")
    print("\nRendered system prompt:\n")
    print(prepared.rendered_prompt.system_prompt)
    print("\nRendered generation prompt (not submitted to an LLM):\n")
    print(prepared.rendered_prompt.task_prompt)
    print(f"\nFinal status: {prepared.status.value}")


def _print_generation(
    result: GenerationResult,
    provider_runs: list[GenerationRun],
) -> None:
    print("\n" + "=" * 80)
    print("Provider attempts:")
    for run in provider_runs:
        detail = f" - {run.error_message}" if run.error_message else ""
        print(f"  {run.provider} / {run.model}: {run.status.value}{detail}")
    print(f"Generation run ID: {result.generation_run_id}")
    print(f"Provider: {result.provider}")
    print(f"Model: {result.model}")
    print(f"Latency: {result.latency_ms} ms")
    if result.input_tokens is not None:
        print(f"Input tokens: {result.input_tokens}")
    if result.output_tokens is not None:
        print(f"Output tokens: {result.output_tokens}")
    print(f"Validation valid: {result.validation.valid}")
    print(
        "Validation errors: "
        + ("; ".join(result.validation.errors) or "<none>")
    )
    print(
        "Validation warnings: "
        + ("; ".join(result.validation.warnings) or "<none>")
    )
    print("\nGenerated SDD section:\n")
    print(result.generated_text or "<empty>")
    print(f"\nFinal request status: {result.status.value}")


def main() -> None:
    args = _parser().parse_args()
    engine = None
    prepared: PreparedGenerationRequest | None = None
    try:
        engine = build_engine()
        session_factory = build_session_factory(engine)
        retrieval = _retrieval_coordinator(args)
        with Neo4jClient.from_environment() as neo4j_client:
            neo4j_client.verify_connectivity()
            graph_enrichment = GraphEnrichmentService(
                GraphRepository(neo4j_client)
            )
            with session_factory() as session:
                orchestrator = GenerationOrchestrator(
                    retrieval,
                    graph_enrichment,
                    PromptTemplateRepository(session),
                    GenerationRequestRepository(session),
                    ContextBuilder(args.max_document_chunks),
                )
                prepared = orchestrator.prepare_generation_context(
                    args.requirement,
                    args.section,
                    args.top_k,
                    args.hops,
                )
                _print_prepared(prepared)
                if args.generate:
                    run_repository = GenerationRunRepository(session)
                    generation_service = GenerationService(
                        build_llm_clients(),
                        GenerationRequestRepository(session),
                        run_repository,
                    )
                    result = generation_service.generate(prepared)
                    _print_generation(
                        result,
                        run_repository.list_for_request(prepared.request_id),
                    )
    except (DatabaseConfigurationError, GraphConfigurationError) as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    except (Neo4jError, ServiceUnavailable) as exc:
        raise SystemExit(f"Neo4j query failed: {exc}") from exc
    except (LookupError, ValueError) as exc:
        raise SystemExit(f"Context preparation failed: {exc}") from exc
    except GenerationError as exc:
        if prepared is not None:
            print(f"Request ID: {prepared.request_id}")
            print("Final request status: FAILED")
        raise SystemExit(f"Generation failed: {exc}") from exc
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
