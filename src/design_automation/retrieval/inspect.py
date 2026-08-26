"""Inspect FAISS, Bedrock fallback, and optional local diagnostics."""

import argparse
from pathlib import Path

from design_automation.core import (
    AWS_REGION,
    BEDROCK_KNOWLEDGE_BASE_ID,
    BEDROCK_RESULT_COUNT,
    DEFAULT_DENSE_CANDIDATE_COUNT,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LEXICAL_CANDIDATE_COUNT,
    DEFAULT_RETRIEVAL_MODE,
    DEFAULT_RRF_CONSTANT,
    DEFAULT_TOP_K,
    FAISS_INDEX_PATH,
    FAISS_FALLBACK_SIMILARITY_THRESHOLD,
    FAISS_MAPPING_PATH,
)
from design_automation.ingestion import ingest_documents
from design_automation.models import RetrievalMode, RetrievalResult
from design_automation.retrieval.bedrock import (
    BedrockConfigurationError,
    BedrockKnowledgeBaseRetriever,
    BedrockRetrievalError,
)
from design_automation.retrieval.coordinator import (
    RetrievalConfig,
    RetrievalCoordinator,
)
from design_automation.retrieval.embeddings import SentenceTransformerEmbeddingService
from design_automation.retrieval.lexical import BM25Retriever
from design_automation.retrieval.retriever import SemanticRetriever
from design_automation.retrieval.vector_store import FaissVectorStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query FAISS, Bedrock Knowledge Bases, or local diagnostics."
    )
    parser.add_argument("query", nargs="?", help="Natural-language retrieval query")
    parser.add_argument("--rebuild", action="store_true", help="Re-embed the corpus")
    parser.add_argument("--model-name", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--device", default=DEFAULT_EMBEDDING_DEVICE)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_EMBEDDING_BATCH_SIZE)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in RetrievalMode],
        default=DEFAULT_RETRIEVAL_MODE,
    )
    parser.add_argument(
        "--fallback-threshold",
        type=float,
        default=FAISS_FALLBACK_SIMILARITY_THRESHOLD,
    )
    parser.add_argument("--rrf-constant", type=int, default=DEFAULT_RRF_CONSTANT)
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
    parser.add_argument("--index-path", type=Path, default=FAISS_INDEX_PATH)
    parser.add_argument("--mapping-path", type=Path, default=FAISS_MAPPING_PATH)
    return parser


def _build_store(
    embedding_service: SentenceTransformerEmbeddingService,
    batch_size: int,
) -> FaissVectorStore:
    ingestion = ingest_documents()
    retriever = SemanticRetriever.build(
        ingestion.chunks,
        embedding_service,
        batch_size,
    )
    return retriever.vector_store


def _print_result(rank: int, result: RetrievalResult) -> None:
    print("\n" + "=" * 80)
    print(f"Rank: {rank}")
    print(f"Provider: {result.provider.value}")
    if result.provider_score is not None:
        print(f"Provider score: {result.provider_score:.6f}")
    print(f"Origin: {result.origin.value}")
    if result.dense_rank is not None:
        print(f"Dense rank: {result.dense_rank}")
    if result.dense_score is not None:
        print(f"Dense score: {result.dense_score:.6f}")
    if result.lexical_rank is not None:
        print(f"Lexical rank: {result.lexical_rank}")
    if result.lexical_score is not None:
        print(f"Lexical score: {result.lexical_score:.6f}")
    if result.fusion_score is not None:
        print(f"RRF score: {result.fusion_score:.6f}")
    print(f"Document: {result.metadata.get('title', result.document_id)}")
    print(f"Source: {result.source}")
    print(f"Section: {result.section or '<none>'}")
    print(f"Chunk ID: {result.chunk_id}")
    print("Text:")
    print(result.text)


def main() -> None:
    """Build or load the index, then optionally run a ranked query."""

    args = _parser().parse_args()
    selected_mode = RetrievalMode(args.mode)
    embedding_service: SentenceTransformerEmbeddingService | None = None
    faiss_retriever: SemanticRetriever | None = None
    chunks = []
    store: FaissVectorStore | None = None

    if selected_mode is RetrievalMode.BEDROCK:
        stored_model = "<not used in Bedrock mode>"
    elif selected_mode is RetrievalMode.LEXICAL:
        chunks = ingest_documents().chunks
        stored_model = "<not used in lexical mode>"
    else:
        index_exists = args.index_path.exists()
        mapping_exists = args.mapping_path.exists()
        should_build = args.rebuild or not (index_exists and mapping_exists)
        if should_build:
            embedding_service = SentenceTransformerEmbeddingService(
                args.model_name,
                args.device,
            )
            store = _build_store(embedding_service, args.batch_size)
            store.save(args.index_path, args.mapping_path, embedding_service.model_name)
            stored_model = embedding_service.model_name
        else:
            store, stored_model = FaissVectorStore.load(
                args.index_path,
                args.mapping_path,
            )
            if stored_model != args.model_name:
                raise ValueError(
                    "Persisted embedding model differs from --model-name; use --rebuild"
                )
        chunks = list(store.chunks)

    print(f"Embedding model: {stored_model}")
    if store is not None:
        print(f"Embedding dimension: {store.dimension}")
        print(f"FAISS index: {args.index_path}")
        print(f"Chunk mapping: {args.mapping_path}")
    if selected_mode is not RetrievalMode.BEDROCK:
        print(f"Corpus chunks: {len(chunks)}")
    print(f"Requested mode: {selected_mode.value}")

    if args.query is None:
        return

    if selected_mode not in (RetrievalMode.LEXICAL, RetrievalMode.BEDROCK):
        if embedding_service is None:
            embedding_service = SentenceTransformerEmbeddingService(
                stored_model,
                args.device,
            )
        if store is None:
            raise ValueError("Dense index was not initialized")
        faiss_retriever = SemanticRetriever(embedding_service, store)

    lexical_retriever = (
        BM25Retriever(chunks)
        if selected_mode in (RetrievalMode.LEXICAL, RetrievalMode.HYBRID)
        else None
    )
    try:
        bedrock_retriever = None
        if (
            selected_mode in (RetrievalMode.BEDROCK, RetrievalMode.FALLBACK)
            and BEDROCK_KNOWLEDGE_BASE_ID
            and AWS_REGION
        ):
            bedrock_retriever = BedrockKnowledgeBaseRetriever(
                knowledge_base_id=BEDROCK_KNOWLEDGE_BASE_ID,
                region=AWS_REGION,
                default_result_count=BEDROCK_RESULT_COUNT,
            )
        coordinator = RetrievalCoordinator(
            faiss_retriever=faiss_retriever,
            bedrock_retriever=bedrock_retriever,
            lexical_retriever=lexical_retriever,
            config=RetrievalConfig(
                faiss_fallback_threshold=args.fallback_threshold,
                bedrock_result_count=BEDROCK_RESULT_COUNT,
                rrf_constant=args.rrf_constant,
                dense_candidate_count=args.dense_candidates,
                lexical_candidate_count=args.lexical_candidates,
            ),
        )
        response = coordinator.retrieve(args.query, selected_mode, args.top_k)
    except (BedrockConfigurationError, BedrockRetrievalError) as exc:
        raise SystemExit(f"Bedrock retrieval unavailable: {exc}") from exc

    print(f"\nQuery: {args.query}")
    print(f"Requested top-k: {args.top_k}")
    print(f"Path used: {response.path_used.value}")
    print(f"Fallback triggered: {response.fallback_triggered}")
    if response.faiss_top_score is not None:
        print(f"FAISS top score: {response.faiss_top_score:.6f}")
    if response.fallback_threshold is not None:
        print(f"Fallback threshold: {response.fallback_threshold:.6f}")
    if response.final_provider is not None:
        print(f"Final provider: {response.final_provider.value}")
    print(f"Decision: {response.reason}")
    for rank, result in enumerate(response.results, start=1):
        _print_result(rank, result)


if __name__ == "__main__":
    main()
