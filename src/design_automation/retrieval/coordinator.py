"""Canonical FAISS-to-Bedrock fallback and optional local diagnostics."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Protocol

from design_automation.core import (
    BEDROCK_RESULT_COUNT,
    DEFAULT_DENSE_CANDIDATE_COUNT,
    DEFAULT_LEXICAL_CANDIDATE_COUNT,
    DEFAULT_RETRIEVAL_MODE,
    DEFAULT_RRF_CONSTANT,
    DEFAULT_TOP_K,
    FAISS_FALLBACK_SIMILARITY_THRESHOLD,
)
from design_automation.models import (
    RetrievalMode,
    RetrievalOrigin,
    RetrievalProvider,
    RetrievalResponse,
    RetrievalResult,
)
from design_automation.retrieval.bedrock import BedrockConfigurationError
from design_automation.retrieval.fusion import reciprocal_rank_fusion
from design_automation.retrieval.lexical import BM25Retriever


ERROR_CODE_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
DOCUMENT_ID_PATTERN = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+){2,}\b")
HTTP_ROUTE_PATTERN = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/", re.I)
ROUTE_PATTERN = re.compile(r"(?<!\w)/[A-Za-z0-9_{}.-]+(?:/[A-Za-z0-9_{}.-]+)*")


class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int) -> list[RetrievalResult]: ...


@dataclass(frozen=True)
class RetrievalConfig:
    """Visible policy controls; the FAISS threshold needs corpus calibration."""

    faiss_fallback_threshold: float = FAISS_FALLBACK_SIMILARITY_THRESHOLD
    bedrock_result_count: int = BEDROCK_RESULT_COUNT
    rrf_constant: int = DEFAULT_RRF_CONSTANT
    dense_candidate_count: int = DEFAULT_DENSE_CANDIDATE_COUNT
    lexical_candidate_count: int = DEFAULT_LEXICAL_CANDIDATE_COUNT

    def __post_init__(self) -> None:
        if self.bedrock_result_count <= 0:
            raise ValueError("bedrock_result_count must be positive")
        if self.rrf_constant < 0:
            raise ValueError("rrf_constant cannot be negative")
        if self.dense_candidate_count <= 0:
            raise ValueError("dense_candidate_count must be positive")
        if self.lexical_candidate_count <= 0:
            raise ValueError("lexical_candidate_count must be positive")


def is_identifier_heavy(query: str) -> bool:
    """Recognize identifiers for optional local BM25 diagnostics only."""

    return any(
        pattern.search(query)
        for pattern in (
            ERROR_CODE_PATTERN,
            DOCUMENT_ID_PATTERN,
            HTTP_ROUTE_PATTERN,
            ROUTE_PATTERN,
        )
    )


def _faiss_score(result: RetrievalResult) -> float | None:
    return result.dense_score if result.dense_score is not None else result.similarity_score


def _annotate_faiss(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return [
        replace(
            result,
            provider=RetrievalProvider.FAISS,
            rank=rank,
            provider_score=_faiss_score(result),
            dense_rank=rank,
            dense_score=_faiss_score(result),
            origin=RetrievalOrigin.DENSE,
        )
        for rank, result in enumerate(results, start=1)
    ]


class RetrievalCoordinator:
    """Route canonical retrieval and preserve optional BM25/RRF diagnostics."""

    def __init__(
        self,
        faiss_retriever: Retriever | None,
        bedrock_retriever: Retriever | None = None,
        lexical_retriever: BM25Retriever | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._faiss = faiss_retriever
        self._bedrock = bedrock_retriever
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

        if selected_mode is RetrievalMode.BEDROCK:
            results = self._retrieve_bedrock(query, top_k)
            return RetrievalResponse(
                requested_mode=selected_mode,
                path_used=RetrievalMode.BEDROCK,
                reason="Bedrock Knowledge Bases mode was explicitly selected.",
                results=results,
                final_provider=RetrievalProvider.BEDROCK_KB,
            )

        if selected_mode is RetrievalMode.LEXICAL:
            results = self._require_lexical().retrieve(query, top_k)
            return RetrievalResponse(
                requested_mode=selected_mode,
                path_used=RetrievalMode.LEXICAL,
                reason="Local BM25 diagnostic mode was explicitly selected.",
                results=results,
                final_provider=RetrievalProvider.BM25,
            )

        faiss_retriever = self._require_faiss()
        if selected_mode is RetrievalMode.FAISS:
            results = _annotate_faiss(faiss_retriever.retrieve(query, top_k))
            return RetrievalResponse(
                requested_mode=selected_mode,
                path_used=RetrievalMode.FAISS,
                reason="Local FAISS mode was explicitly selected.",
                results=results,
                faiss_top_score=_faiss_score(results[0]) if results else None,
                final_provider=RetrievalProvider.FAISS,
            )

        dense_count = max(top_k, self._config.dense_candidate_count)
        faiss_results = _annotate_faiss(faiss_retriever.retrieve(query, dense_count))
        faiss_top_score = _faiss_score(faiss_results[0]) if faiss_results else None

        if selected_mode is RetrievalMode.FALLBACK:
            fallback_reason = self._fallback_reason(faiss_top_score)
            if fallback_reason is not None:
                results = self._retrieve_bedrock(query, top_k)
                return RetrievalResponse(
                    requested_mode=selected_mode,
                    path_used=RetrievalMode.BEDROCK,
                    reason=fallback_reason,
                    results=results,
                    fallback_triggered=True,
                    faiss_top_score=faiss_top_score,
                    fallback_threshold=self._config.faiss_fallback_threshold,
                    final_provider=RetrievalProvider.BEDROCK_KB,
                )
            return RetrievalResponse(
                requested_mode=selected_mode,
                path_used=RetrievalMode.FAISS,
                reason=(
                    f"FAISS results retained: top score {faiss_top_score:.6f} "
                    f"meets threshold {self._config.faiss_fallback_threshold:.6f}."
                ),
                results=faiss_results[:top_k],
                faiss_top_score=faiss_top_score,
                fallback_threshold=self._config.faiss_fallback_threshold,
                final_provider=RetrievalProvider.FAISS,
            )

        lexical = self._require_lexical()
        lexical_count = max(top_k, self._config.lexical_candidate_count)
        lexical_results = lexical.retrieve(query, lexical_count)
        fused = reciprocal_rank_fusion(
            faiss_results,
            lexical_results,
            top_k,
            self._config.rrf_constant,
        )
        return RetrievalResponse(
            requested_mode=selected_mode,
            path_used=RetrievalMode.HYBRID,
            reason=(
                "Local diagnostic mode fused FAISS and BM25 ranks with "
                f"RRF constant {self._config.rrf_constant}."
            ),
            results=fused,
            faiss_top_score=faiss_top_score,
            final_provider=RetrievalProvider.DIAGNOSTIC_RRF,
        )

    def _retrieve_bedrock(self, query: str, top_k: int) -> list[RetrievalResult]:
        result_count = max(top_k, self._config.bedrock_result_count)
        return self._require_bedrock().retrieve(query, result_count)[:top_k]

    def _require_faiss(self) -> Retriever:
        if self._faiss is None:
            raise ValueError("This retrieval mode requires a FAISS retriever")
        return self._faiss

    def _require_bedrock(self) -> Retriever:
        if self._bedrock is None:
            raise BedrockConfigurationError(
                "Bedrock retrieval is required but is not configured. Set "
                "BEDROCK_KNOWLEDGE_BASE_ID and AWS_REGION (or "
                "AWS_DEFAULT_REGION), then configure AWS credentials."
            )
        return self._bedrock

    def _require_lexical(self) -> BM25Retriever:
        if self._lexical is None:
            raise ValueError("This diagnostic mode requires the local BM25 retriever")
        return self._lexical

    def _fallback_reason(self, faiss_top_score: float | None) -> str | None:
        if faiss_top_score is None:
            return "Bedrock fallback triggered because FAISS returned no results."
        if faiss_top_score < self._config.faiss_fallback_threshold:
            return (
                "Bedrock fallback triggered because FAISS top score "
                f"{faiss_top_score:.6f} is below threshold "
                f"{self._config.faiss_fallback_threshold:.6f}."
            )
        return None
