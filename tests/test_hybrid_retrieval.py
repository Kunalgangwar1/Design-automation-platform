"""Tests for canonical fallback and optional local diagnostics."""

from dataclasses import dataclass

import pytest

from design_automation.models import (
    DocumentChunk,
    RetrievalMode,
    RetrievalOrigin,
    RetrievalProvider,
    RetrievalResult,
)
from design_automation.retrieval.coordinator import (
    RetrievalConfig,
    RetrievalCoordinator,
    is_identifier_heavy,
)
from design_automation.retrieval.bedrock import BedrockRetrievalError
from design_automation.retrieval.fusion import reciprocal_rank_fusion
from design_automation.retrieval.lexical import BM25Retriever


def _chunk(index: int, section: str, text: str) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=f"chunk-{index}",
        document_id="doc-1",
        chunk_index=index,
        section=section,
        text=text,
        metadata={
            "title": "Refund Design",
            "source_path": "interfaces/refunds.md",
            "owner": "Commerce Architecture",
        },
    )


def _corpus() -> list[DocumentChunk]:
    return [
        _chunk(0, "Overview", "General order processing guidance."),
        _chunk(
            1,
            "Refund API",
            "Use POST /orders/{order_id}/refunds for an item refund.",
        ),
        _chunk(
            2,
            "Errors",
            "Return IDEMPOTENCY_CONFLICT when a key has different request data.",
        ),
    ]


def _dense_result(chunk: DocumentChunk, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        section=chunk.section,
        source=chunk.metadata["source_path"],
        text=chunk.text,
        similarity_score=score,
        metadata=dict(chunk.metadata),
        dense_score=score,
        origin=RetrievalOrigin.DENSE,
    )


@dataclass
class FakeRetriever:
    results: list[RetrievalResult]
    calls: int = 0
    requested_top_k: int | None = None

    def retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        self.calls += 1
        self.requested_top_k = top_k
        return self.results[:top_k]


class FailingRetriever:
    def retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        raise BedrockRetrievalError("simulated Bedrock failure")


def _bedrock_result(chunk: DocumentChunk, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=f"bedrock-{chunk.chunk_id}",
        document_id=chunk.document_id,
        section=chunk.section,
        source="s3://enterprise/refunds.md",
        text=chunk.text,
        similarity_score=None,
        metadata=dict(chunk.metadata),
        provider=RetrievalProvider.BEDROCK_KB,
        provider_score=score,
    )


def test_lexical_retrieval_finds_exact_identifier_and_preserves_metadata() -> None:
    retriever = BM25Retriever(_corpus())

    result = retriever.retrieve("IDEMPOTENCY_CONFLICT", top_k=1)[0]

    assert result.section == "Errors"
    assert result.lexical_rank == 1
    assert result.lexical_score is not None
    assert result.origin is RetrievalOrigin.LEXICAL
    assert result.metadata["owner"] == "Commerce Architecture"


def test_lexical_api_route_ranking_is_deterministic() -> None:
    retriever = BM25Retriever(_corpus())
    query = "POST /orders/{order_id}/refunds"

    first_run = retriever.retrieve(query, top_k=3)
    second_run = retriever.retrieve(query, top_k=3)

    assert first_run[0].section == "Refund API"
    assert [result.chunk_id for result in first_run] == [
        result.chunk_id for result in second_run
    ]


@pytest.mark.parametrize(
    "query",
    ["IDEMPOTENCY_CONFLICT", "OMS-REF-001", "POST /orders/123/refunds", "/refunds/123"],
)
def test_identifier_heavy_query_shapes_are_recognized(query: str) -> None:
    assert is_identifier_heavy(query)


def test_identifier_does_not_override_canonical_faiss_sufficiency_policy() -> None:
    chunks = _corpus()
    faiss = FakeRetriever([_dense_result(chunks[0], 0.95)])
    bedrock = FakeRetriever([_bedrock_result(chunks[2], 0.9)])
    coordinator = RetrievalCoordinator(faiss, bedrock)

    response = coordinator.retrieve(
        "Explain IDEMPOTENCY_CONFLICT",
        RetrievalMode.FALLBACK,
        top_k=1,
    )

    assert faiss.calls == 1
    assert bedrock.calls == 0
    assert not response.fallback_triggered
    assert response.path_used is RetrievalMode.FAISS


def test_low_faiss_score_triggers_bedrock_fallback() -> None:
    chunks = _corpus()
    faiss = FakeRetriever([_dense_result(chunks[0], 0.30)])
    bedrock = FakeRetriever([_bedrock_result(chunks[1], 0.77)])
    coordinator = RetrievalCoordinator(
        faiss,
        bedrock,
        config=RetrievalConfig(
            faiss_fallback_threshold=0.50,
            bedrock_result_count=3,
        ),
    )

    response = coordinator.retrieve("item refund API", "fallback", top_k=1)

    assert response.fallback_triggered
    assert response.path_used is RetrievalMode.BEDROCK
    assert response.final_provider is RetrievalProvider.BEDROCK_KB
    assert response.results[0].section == "Refund API"
    assert bedrock.requested_top_k == 3
    assert "below threshold" in response.reason


def test_empty_faiss_results_trigger_bedrock_fallback() -> None:
    chunks = _corpus()
    bedrock = FakeRetriever([_bedrock_result(chunks[1], 0.77)])
    coordinator = RetrievalCoordinator(FakeRetriever([]), bedrock)

    response = coordinator.retrieve("item refund API", "fallback", top_k=1)

    assert response.fallback_triggered
    assert response.path_used is RetrievalMode.BEDROCK
    assert "no results" in response.reason


def test_bedrock_failure_during_fallback_is_explicit() -> None:
    coordinator = RetrievalCoordinator(FakeRetriever([]), FailingRetriever())

    with pytest.raises(BedrockRetrievalError, match="simulated Bedrock failure"):
        coordinator.retrieve("item refund API", "fallback", top_k=1)


def test_strong_dense_score_does_not_trigger_fallback() -> None:
    chunks = _corpus()
    faiss = FakeRetriever([_dense_result(chunks[0], 0.85)])
    bedrock = FakeRetriever([_bedrock_result(chunks[1], 0.99)])
    coordinator = RetrievalCoordinator(
        faiss,
        bedrock,
        config=RetrievalConfig(faiss_fallback_threshold=0.50),
    )

    response = coordinator.retrieve("general order guidance", "fallback", top_k=1)

    assert not response.fallback_triggered
    assert response.path_used is RetrievalMode.FAISS
    assert response.results[0].chunk_id == chunks[0].chunk_id
    assert bedrock.calls == 0


def test_default_mode_is_fallback() -> None:
    chunks = _corpus()
    faiss = FakeRetriever([_dense_result(chunks[0], 0.85)])
    coordinator = RetrievalCoordinator(
        faiss,
        FakeRetriever([_bedrock_result(chunks[1], 0.9)]),
        config=RetrievalConfig(faiss_fallback_threshold=0.50),
    )

    response = coordinator.retrieve("general order guidance", top_k=1)

    assert response.requested_mode is RetrievalMode.FALLBACK
    assert response.path_used is RetrievalMode.FAISS


def test_rrf_combines_ranks_and_deduplicates_shared_chunks() -> None:
    chunks = _corpus()
    dense = [_dense_result(chunks[0], 0.9), _dense_result(chunks[1], 0.8)]
    lexical_retriever = BM25Retriever(chunks)
    lexical = lexical_retriever.retrieve(
        "POST /orders/{order_id}/refunds",
        top_k=2,
    )

    fused = reciprocal_rank_fusion(dense, lexical, top_k=3, rrf_constant=60)

    assert len({result.chunk_id for result in fused}) == len(fused)
    shared = next(result for result in fused if result.chunk_id == chunks[1].chunk_id)
    assert shared.origin is RetrievalOrigin.BOTH
    assert shared.dense_rank == 2
    assert shared.lexical_rank == 1
    assert shared.fusion_score == pytest.approx(1 / 62 + 1 / 61)
    assert shared.metadata == chunks[1].metadata


def test_hybrid_coordinator_returns_fused_top_k() -> None:
    chunks = _corpus()
    dense = FakeRetriever(
        [_dense_result(chunks[0], 0.9), _dense_result(chunks[1], 0.8)]
    )
    coordinator = RetrievalCoordinator(
        faiss_retriever=dense,
        lexical_retriever=BM25Retriever(chunks),
    )

    response = coordinator.retrieve("item refund API", "hybrid", top_k=2)

    assert response.path_used is RetrievalMode.HYBRID
    assert len(response.results) == 2
    assert response.results[0].fusion_score is not None
