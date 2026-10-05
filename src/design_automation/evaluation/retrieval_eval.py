"""CLI for the repeatable dense/BM25/hybrid retrieval baseline."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import replace
from pathlib import Path

from design_automation.core import (
    DEFAULT_DENSE_CANDIDATE_COUNT,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LEXICAL_CANDIDATE_COUNT,
    DEFAULT_RRF_CONSTANT,
    FAISS_INDEX_PATH,
    FAISS_MAPPING_PATH,
    PROJECT_ROOT,
    RERANKER_CANDIDATE_K,
    RERANKER_MODEL,
)
from design_automation.evaluation.dataset import load_gold_dataset
from design_automation.evaluation.models import (
    HYBRID_RERANKED_MODE,
    RetrievalEvaluationReport,
)
from design_automation.evaluation.retrieval_runner import (
    evaluate_retrieval,
    write_report,
)
from design_automation.evaluation.reranker import (
    CrossEncoderReranker,
    CrossEncoderScorer,
    RerankingEvaluationRetriever,
)
from design_automation.ingestion import ingest_documents
from design_automation.models import DocumentChunk, RetrievalMode
from design_automation.retrieval.coordinator import RetrievalConfig, RetrievalCoordinator
from design_automation.retrieval.embeddings import SentenceTransformerEmbeddingService
from design_automation.retrieval.lexical import BM25Retriever
from design_automation.retrieval.retriever import SemanticRetriever
from design_automation.retrieval.vector_store import FaissVectorStore


DEFAULT_DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "oms_rag_gold.json"
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "artifacts" / "evaluation" / "retrieval_baseline.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the real FAISS, BM25, and RRF retrieval baseline."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print metrics without writing the immutable baseline artifact.",
    )
    parser.add_argument("--index-path", type=Path, default=FAISS_INDEX_PATH)
    parser.add_argument("--mapping-path", type=Path, default=FAISS_MAPPING_PATH)
    parser.add_argument("--model-name", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--device", default=DEFAULT_EMBEDDING_DEVICE)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_EMBEDDING_BATCH_SIZE)
    parser.add_argument(
        "--reranker-model",
        nargs="?",
        const=RERANKER_MODEL,
        help=(
            "Run the optional CrossEncoder experiment; optionally provide a "
            "sentence-transformers CrossEncoder model name."
        ),
    )
    parser.add_argument(
        "--reranker-candidates",
        type=int,
        default=RERANKER_CANDIDATE_K,
    )
    return parser


def _coordinator(
    args: argparse.Namespace,
) -> tuple[RetrievalCoordinator, list[DocumentChunk], str]:
    if args.index_path.exists() and args.mapping_path.exists():
        store, stored_model = FaissVectorStore.load(
            args.index_path,
            args.mapping_path,
        )
        if stored_model != args.model_name:
            raise ValueError(
                "Persisted embedding model differs from --model-name; rebuild it "
                "with the retrieval inspection command first."
            )
        chunks = list(store.chunks)
    else:
        chunks = ingest_documents().chunks
        embedding_service = SentenceTransformerEmbeddingService(
            args.model_name,
            args.device,
        )
        dense = SemanticRetriever.build(chunks, embedding_service, args.batch_size)
        store = dense.vector_store
        store.save(args.index_path, args.mapping_path, embedding_service.model_name)
        stored_model = embedding_service.model_name

    embedding_service = SentenceTransformerEmbeddingService(
        stored_model,
        args.device,
    )
    dense = SemanticRetriever(embedding_service, store)
    coordinator = RetrievalCoordinator(
        dense,
        BM25Retriever(chunks),
        RetrievalConfig(
            rrf_constant=DEFAULT_RRF_CONSTANT,
            dense_candidate_count=DEFAULT_DENSE_CANDIDATE_COUNT,
            lexical_candidate_count=DEFAULT_LEXICAL_CANDIDATE_COUNT,
        ),
    )
    return coordinator, chunks, stored_model


def _print_aggregates(report: RetrievalEvaluationReport) -> None:
    print(f"Dataset: {report.corpus} v{report.dataset_version}")
    print(f"Cases: {report.case_count}")
    print(f"Embedding model: {report.embedding_model}")
    print("\nAggregate retrieval metrics")
    columns = ["mode"]
    for k in report.ks:
        columns.extend([f"P@{k}", f"R@{k}", f"Hit@{k}"])
    columns.extend(["MRR@10", "candidate_ms", "reranker_ms", "total_ms"])
    print(" | ".join(columns))
    print(" | ".join("---" for _ in columns))
    for mode in report.aggregates:
        aggregate = report.aggregates[mode]
        values = [mode]
        for k in report.ks:
            metric = aggregate.metrics_at_k[k]
            values.extend(
                [
                    f"{metric.precision:.3f}",
                    f"{metric.recall:.3f}",
                    f"{metric.hit_rate:.3f}",
                ]
            )
        values.extend(
            [
                f"{aggregate.mrr:.3f}",
                (
                    f"{aggregate.average_candidate_retrieval_latency_ms:.2f}"
                    if aggregate.average_candidate_retrieval_latency_ms is not None
                    else "-"
                ),
                (
                    f"{aggregate.average_reranker_latency_ms:.2f}"
                    if aggregate.average_reranker_latency_ms is not None
                    else "-"
                ),
                f"{aggregate.average_latency_ms:.2f}",
            ]
        )
        print(" | ".join(values))


def _print_queries(report: RetrievalEvaluationReport) -> None:
    print("\nPer-query ranking report")
    for row in report.queries:
        ranks = " ".join(
            f"{mode}={row.modes[mode].first_relevant_rank or '-'}"
            for mode in row.modes
        )
        hits = " ".join(
            f"{mode}={'yes' if (row.modes[mode].first_relevant_rank or 99) <= 5 else 'no'}"
            for mode in row.modes
        )
        print(f"\n{row.case_id} [{row.category}]")
        print(f"  query: {row.query}")
        print(f"  relevant: {', '.join(row.relevant_chunk_ids)}")
        print(f"  first relevant rank: {ranks}")
        print(f"  hit@5: {hits}")
        for mode in row.modes:
            print(
                f"  {mode} top IDs: "
                + ", ".join(row.modes[mode].retrieved_chunk_ids[:5])
            )
        if row.diagnostics:
            print(f"  diagnostics: {', '.join(row.diagnostics)}")


def _print_reranker_analysis(report: RetrievalEvaluationReport) -> None:
    if HYBRID_RERANKED_MODE not in report.aggregates:
        return
    known_cases = {
        "support-partial-refund-api",
        "gateway-timeout-reconciliation",
        "identifier-requirement-id",
        "shipping-first-release",
        "transactional-outbox",
    }
    print("\nHybrid versus CrossEncoder first-relevant ranks")
    for row in report.queries:
        if row.case_id in known_cases:
            print(
                f"{row.case_id}: hybrid="
                f"{row.modes['hybrid'].first_relevant_rank or '-'} "
                f"hybrid_reranked="
                f"{row.modes[HYBRID_RERANKED_MODE].first_relevant_rank or '-'}"
            )
    regressions = [
        row.case_id
        for row in report.queries
        if "reranker_hurts_hybrid_rank" in row.diagnostics
    ]
    print(
        "Reranker regressions: "
        + (", ".join(regressions) if regressions else "none")
    )


def main() -> None:
    args = _parser().parse_args()
    if args.reranker_candidates <= 0:
        raise ValueError("--reranker-candidates must be positive")
    if args.reranker_model and args.output == DEFAULT_OUTPUT_PATH:
        args.output = (
            PROJECT_ROOT
            / "artifacts"
            / "evaluation"
            / "retrieval_hybrid_reranked.json"
        )
    if not args.no_save and args.output.exists():
        raise FileExistsError(
            f"Baseline already exists and will not be overwritten: {args.output}. "
            "Use --no-save or choose a new --output path."
        )
    coordinator, chunks, embedding_model = _coordinator(args)
    known_chunk_ids = {chunk.chunk_id for chunk in chunks}
    dataset = load_gold_dataset(args.dataset, known_chunk_ids)
    evaluation_retriever = coordinator
    modes: tuple[RetrievalMode | str, ...] = (
        RetrievalMode.DENSE,
        RetrievalMode.LEXICAL,
        RetrievalMode.HYBRID,
    )
    if args.reranker_model:
        evaluation_retriever = RerankingEvaluationRetriever(
            coordinator,
            CrossEncoderReranker(
                CrossEncoderScorer(args.reranker_model, args.device)
            ),
            args.reranker_candidates,
        )
        modes = (*modes, HYBRID_RERANKED_MODE)
    report = evaluate_retrieval(evaluation_retriever, dataset, modes=modes)
    report = replace(
        report,
        dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        embedding_model=(
            embedding_model
            if not args.reranker_model
            else f"{embedding_model}; reranker={args.reranker_model}"
        ),
        corpus_chunk_ids=tuple(chunk.chunk_id for chunk in chunks),
    )
    _print_aggregates(report)
    _print_queries(report)
    _print_reranker_analysis(report)
    if not args.no_save:
        write_report(args.output, report)
        print(f"\nEvaluation report written once to: {args.output}")


if __name__ == "__main__":
    main()
