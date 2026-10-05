"""Coordinate dense, lexical, and hybrid local retrieval."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from design_automation.core import (
    DEFAULT_DENSE_CANDIDATE_COUNT,
    DEFAULT_LEXICAL_CANDIDATE_COUNT,
    DEFAULT_RETRIEVAL_MODE,
    DEFAULT_RRF_CONSTANT,
    DEFAULT_TOP_K,
)
from design_automation.models import (
    RetrievalMode,
    RetrievalOrigin,
    RetrievalProvider,
    RetrievalResponse,
    RetrievalResult,
)
from design_automation.retrieval.fusion import reciprocal_rank_fusion


class Retriever(Protocol):
    """The small interface shared by FAISS and BM25 retrievers."""

    def retrieve(self, query: str, top_k: int) -> list[RetrievalResult]: ...


@dataclass(frozen=True)
class RetrievalConfig:
    """Candidate-pool and RRF controls for hybrid retrieval."""

    rrf_constant: int = DEFAULT_RRF_CONSTANT
    dense_candidate_count: int = DEFAULT_DENSE_CANDIDATE_COUNT
    lexical_candidate_count: int = DEFAULT_LEXICAL_CANDIDATE_COUNT

    def __post_init__(self) -> None:
        if self.rrf_constant < 0:
            raise ValueError("rrf_constant cannot be negative")
        if self.dense_candidate_count <= 0:
            raise ValueError("dense_candidate_count must be positive")
        if self.lexical_candidate_count <= 0:
            raise ValueError("lexical_candidate_count must be positive")


def _dense_score(result: RetrievalResult) -> float | None:
    return result.dense_score if result.dense_score is not None else result.similarity_score


def _annotate_dense(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return [
        replace(
            result,
            provider=RetrievalProvider.FAISS,
            rank=rank,
            provider_score=_dense_score(result),
            dense_rank=rank,
            dense_score=_dense_score(result),
            origin=RetrievalOrigin.DENSE,
        )
        for rank, result in enumerate(results, start=1)
    ]


class RetrievalCoordinator:
    """Run a selected local retriever or fuse both candidate rankings."""

    def __init__(
        self,
        dense_retriever: Retriever | None,
        lexical_retriever: Retriever | None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._dense = dense_retriever
        self._lexical = lexical_retriever
        self._config = config or RetrievalConfig()

    def retrieve(
        self,
        query: str,
        mode: RetrievalMode | str = DEFAULT_RETRIEVAL_MODE,
        top_k: int = DEFAULT_TOP_K,
    ) -> RetrievalResponse:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        selected_mode = RetrievalMode(mode)

        if selected_mode is RetrievalMode.DENSE:
            results = _annotate_dense(self._require_dense().retrieve(query, top_k))
            return RetrievalResponse(
                requested_mode=selected_mode,
                path_used=RetrievalMode.DENSE,
                reason="Dense mode used Sentence Transformers and FAISS only.",
                results=results,
                final_top_k=top_k,
                dense_candidate_count=top_k,
            )

        if selected_mode is RetrievalMode.LEXICAL:
            results = self._require_lexical().retrieve(query, top_k)
            return RetrievalResponse(
                requested_mode=selected_mode,
                path_used=RetrievalMode.LEXICAL,
                reason="Lexical mode used BM25 only.",
                results=results,
                final_top_k=top_k,
                lexical_candidate_count=top_k,
            )

        dense_count = max(top_k, self._config.dense_candidate_count)
        lexical_count = max(top_k, self._config.lexical_candidate_count)
        dense_results = _annotate_dense(
            self._require_dense().retrieve(query, dense_count)
        )
        lexical_results = self._require_lexical().retrieve(query, lexical_count)
        results = reciprocal_rank_fusion(
            dense_results,
            lexical_results,
            top_k,
            self._config.rrf_constant,
        )
        return RetrievalResponse(
            requested_mode=selected_mode,
            path_used=RetrievalMode.HYBRID,
            reason=(
                "Hybrid mode fused independent FAISS and BM25 ranks with "
                f"RRF constant {self._config.rrf_constant}."
            ),
            results=results,
            final_top_k=top_k,
            dense_candidate_count=dense_count,
            lexical_candidate_count=lexical_count,
        )

    def _require_dense(self) -> Retriever:
        if self._dense is None:
            raise ValueError("This retrieval mode requires the FAISS retriever")
        return self._dense

    def _require_lexical(self) -> Retriever:
        if self._lexical is None:
            raise ValueError("This retrieval mode requires the BM25 retriever")
        return self._lexical
