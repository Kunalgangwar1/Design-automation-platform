"""Tests for BM25, RRF, and canonical local hybrid retrieval."""

from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path

import pytest

from design_automation.ingestion import ingest_documents
from design_automation.models import (
    DocumentChunk,
    RetrievalMode,
    RetrievalOrigin,
    RetrievalProvider,
    RetrievalResult,
)
from design_automation.retrieval.coordinator import RetrievalConfig, RetrievalCoordinator
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
            "requirement_id": "OMS-REF-001",
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
        _chunk(
            3,
            "Refund event",
            "Publish OrderItemsRefunded after confirmed payment success.",
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
        provider=RetrievalProvider.FAISS,
        dense_score=score,
        origin=RetrievalOrigin.DENSE,
    )


def _lexical_result(
    chunk: DocumentChunk,
    score: float,
    rank: int,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        section=chunk.section,
        source=chunk.metadata["source_path"],
        text=chunk.text,
        similarity_score=None,
        metadata=dict(chunk.metadata),
        provider=RetrievalProvider.BM25,
        lexical_rank=rank,
        lexical_score=score,
        origin=RetrievalOrigin.LEXICAL,
    )


@dataclass
class FakeRetriever:
    results: list[RetrievalResult]
    requested_counts: list[int] = field(default_factory=list)

    def retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        self.requested_counts.append(top_k)
        return self.results[:top_k]


def test_bm25_indexes_every_chunk_and_preserves_metadata() -> None:
    chunks = _corpus()
    retriever = BM25Retriever(chunks)

    result = retriever.retrieve("IDEMPOTENCY_CONFLICT", top_k=1)[0]

    assert retriever.size == len(chunks)
    assert result.section == "Errors"
    assert result.lexical_rank == 1
    assert result.lexical_score is not None
    assert result.provider is RetrievalProvider.BM25
    assert result.metadata["owner"] == "Commerce Architecture"


@pytest.mark.parametrize(
    ("query", "expected_section"),
    [
        ("IDEMPOTENCY_CONFLICT", "Errors"),
        ("OrderItemsRefunded", "Refund event"),
        ("OMS-REF-001", "Overview"),
    ],
)
def test_bm25_preserves_exact_technical_identifiers(
    query: str,
    expected_section: str,
) -> None:
    result = BM25Retriever(_corpus()).retrieve(query, top_k=1)[0]

    assert result.section == expected_section


def test_lexical_api_route_ranking_is_deterministic() -> None:
    retriever = BM25Retriever(_corpus())
    query = "POST /orders/{order_id}/refunds"

    first_run = retriever.retrieve(query, top_k=4)
    second_run = retriever.retrieve(query, top_k=4)

    assert first_run[0].section == "Refund API"
    assert [result.chunk_id for result in first_run] == [
        result.chunk_id for result in second_run
    ]


@pytest.mark.parametrize(
    ("query", "expected_section"),
    [
        ("IDEMPOTENCY_CONFLICT", "Support Portal to Order Service"),
        ("OrderItemsRefunded", "OrderItemsRefunded event"),
        ("OMS-REF-001", "Business statement"),
    ],
)
def test_actual_fixture_identifiers_are_lexically_searchable(
    query: str,
    expected_section: str,
) -> None:
    chunks = ingest_documents().chunks

    result = BM25Retriever(chunks).retrieve(query, top_k=1)[0]

    assert result.section == expected_section


def test_rrf_formula_deduplicates_and_preserves_both_rankings() -> None:
    chunks = _corpus()
    dense = [_dense_result(chunks[0], 0.91), _dense_result(chunks[1], 0.82)]
    lexical = [
        _lexical_result(chunks[1], 4.2, 1),
        _lexical_result(chunks[2], 3.7, 2),
    ]

    fused = reciprocal_rank_fusion(dense, lexical, top_k=4, rrf_constant=60)

    assert len(fused) == 3
    assert len({result.chunk_id for result in fused}) == len(fused)
    shared = next(result for result in fused if result.chunk_id == chunks[1].chunk_id)
    assert shared.origin is RetrievalOrigin.BOTH
    assert shared.dense_rank == 2
    assert shared.dense_score == pytest.approx(0.82)
    assert shared.lexical_rank == 1
    assert shared.lexical_score == pytest.approx(4.2)
    assert shared.fusion_score == pytest.approx(1 / 62 + 1 / 61)
    assert shared.provider is RetrievalProvider.RRF
    assert shared.metadata == chunks[1].metadata


def test_rrf_keeps_candidates_returned_by_only_one_retriever() -> None:
    chunks = _corpus()
    fused = reciprocal_rank_fusion(
        [_dense_result(chunks[0], 0.9)],
        [_lexical_result(chunks[2], 5.0, 1)],
        top_k=2,
        rrf_constant=60,
    )

    assert {result.origin for result in fused} == {
        RetrievalOrigin.DENSE,
        RetrievalOrigin.LEXICAL,
    }
    assert all(result.fusion_score == pytest.approx(1 / 61) for result in fused)


def test_hybrid_uses_independent_candidate_counts_and_final_top_k() -> None:
    chunks = _corpus()
    dense = FakeRetriever([_dense_result(chunk, 0.9 - index / 10) for index, chunk in enumerate(chunks)])
    lexical = FakeRetriever(
        [_lexical_result(chunk, 5.0 - index, index + 1) for index, chunk in enumerate(reversed(chunks))]
    )
    coordinator = RetrievalCoordinator(
        dense,
        lexical,
        RetrievalConfig(
            dense_candidate_count=7,
            lexical_candidate_count=8,
            rrf_constant=60,
        ),
    )

    response = coordinator.retrieve("refund", top_k=2)

    assert response.requested_mode is RetrievalMode.HYBRID
    assert response.path_used is RetrievalMode.HYBRID
    assert dense.requested_counts == [7]
    assert lexical.requested_counts == [8]
    assert response.dense_candidate_count == 7
    assert response.lexical_candidate_count == 8
    assert response.final_top_k == 2
    assert len(response.results) == 2
    assert all(result.rank in (1, 2) for result in response.results)


def test_dense_and_lexical_debug_modes_call_only_selected_retriever() -> None:
    chunks = _corpus()
    dense = FakeRetriever([_dense_result(chunks[0], 0.9)])
    lexical = FakeRetriever([_lexical_result(chunks[1], 4.0, 1)])
    coordinator = RetrievalCoordinator(dense, lexical)

    dense_response = coordinator.retrieve("refund", "dense", top_k=1)
    lexical_response = coordinator.retrieve("refund", "lexical", top_k=1)

    assert dense_response.path_used is RetrievalMode.DENSE
    assert lexical_response.path_used is RetrievalMode.LEXICAL
    assert dense.requested_counts == [1]
    assert lexical.requested_counts == [1]


def test_no_bedrock_retrieval_module_or_dependency_remains() -> None:
    project_root = Path(__file__).resolve().parents[1]

    assert find_spec("design_automation.retrieval.bedrock") is None
    assert "boto3" not in (project_root / "pyproject.toml").read_text(encoding="utf-8")
    assert {mode.value for mode in RetrievalMode} == {"dense", "lexical", "hybrid"}
