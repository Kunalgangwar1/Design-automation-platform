"""Build, persist, and inspect custom local retrieval results."""

import argparse
from pathlib import Path

from design_automation.core import (
    DEFAULT_DENSE_CANDIDATE_COUNT,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LEXICAL_CANDIDATE_COUNT,
    DEFAULT_RETRIEVAL_MODE,
    DEFAULT_RRF_CONSTANT,
    DEFAULT_TOP_K,
    FAISS_INDEX_PATH,
    FAISS_MAPPING_PATH,
)
from design_automation.ingestion import ingest_documents
from design_automation.models import RetrievalMode, RetrievalResult
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
        description="Query dense FAISS, lexical BM25, or hybrid RRF retrieval."
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
    print(f"Final rank: {rank}")
    print(f"Provider: {result.provider.value}")
    print(f"Contributing retrieval sources: {result.origin.value}")
    if result.fusion_score is not None:
        print(f"RRF score: {result.fusion_score:.6f}")
    if result.dense_rank is not None:
        print(f"FAISS rank: {result.dense_rank}")
    if result.dense_score is not None:
        print(f"FAISS similarity: {result.dense_score:.6f}")
    if result.lexical_rank is not None:
        print(f"BM25 rank: {result.lexical_rank}")
    if result.lexical_score is not None:
        print(f"BM25 score: {result.lexical_score:.6f}")
    print(f"Document: {result.metadata.get('title', result.document_id)}")
    print(f"Source: {result.source}")
    print(f"Section: {result.section or '<none>'}")
    print(f"Chunk ID: {result.chunk_id}")
    print(f"Metadata: {result.metadata}")
    print("Text:")
    print(result.text)


def main() -> None:
    """Build or load local indexes, then optionally run a ranked query."""

    args = _parser().parse_args()
    selected_mode = RetrievalMode(args.mode)
    embedding_service: SentenceTransformerEmbeddingService | None = None
    dense_retriever: SemanticRetriever | None = None
    store: FaissVectorStore | None = None

    if selected_mode is RetrievalMode.LEXICAL:
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
    print(f"Corpus chunks: {len(chunks)}")
    print(f"Mode: {selected_mode.value}")

    if args.query is None:
        return

    if selected_mode is not RetrievalMode.LEXICAL:
        if embedding_service is None:
            embedding_service = SentenceTransformerEmbeddingService(
                stored_model,
                args.device,
            )
        if store is None:
            raise ValueError("FAISS index was not initialized")
        dense_retriever = SemanticRetriever(embedding_service, store)

    lexical_retriever = (
        BM25Retriever(chunks)
        if selected_mode in (RetrievalMode.LEXICAL, RetrievalMode.HYBRID)
        else None
    )
    coordinator = RetrievalCoordinator(
        dense_retriever=dense_retriever,
        lexical_retriever=lexical_retriever,
        config=RetrievalConfig(
            rrf_constant=args.rrf_constant,
            dense_candidate_count=args.dense_candidates,
            lexical_candidate_count=args.lexical_candidates,
        ),
    )
    response = coordinator.retrieve(args.query, selected_mode, args.top_k)

    print(f"\nQuery: {args.query}")
    print(f"Final top-k: {response.final_top_k}")
    print(f"Dense candidate count: {response.dense_candidate_count}")
    print(f"Lexical candidate count: {response.lexical_candidate_count}")
    print(f"Decision: {response.reason}")
    for rank, result in enumerate(response.results, start=1):
        _print_result(rank, result)


if __name__ == "__main__":
    main()
