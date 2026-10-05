"""Optional CrossEncoder reranking after unchanged hybrid RRF retrieval."""

from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Protocol

from design_automation.core import RERANKER_CANDIDATE_K, RERANKER_MODEL
from design_automation.evaluation.models import HYBRID_RERANKED_MODE
from design_automation.evaluation.retrieval_runner import EvaluationRetriever
from design_automation.models import RetrievalMode, RetrievalResponse, RetrievalResult


class PairScorer(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int,
    ) -> "RerankOutcome": ...


@dataclass(frozen=True)
class RerankOutcome:
    results: list[RetrievalResult]
    latency_ms: float


class CrossEncoderScorer:
    """Load a small sentence-transformers CrossEncoder only when requested."""

    def __init__(self, model_name: str = RERANKER_MODEL, device: str = "cpu") -> None:
        from sentence_transformers import CrossEncoder

        self.model_name = model_name
        self._model: Any = CrossEncoder(model_name, device=device)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        raw_scores = self._model.predict([(query, text) for text in texts])
        return [float(score) for score in raw_scores]


class CrossEncoderReranker:
    """Score and reorder a fixed candidate list without changing its evidence."""

    def __init__(self, scorer: PairScorer) -> None:
        self._scorer = scorer

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int,
    ) -> RerankOutcome:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        started = perf_counter()
        scores = self._scorer.score(query, [candidate.text for candidate in candidates])
        if len(scores) != len(candidates):
            raise ValueError("Reranker returned a different number of scores")
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda item: (-item[1], item[0].rank or 0, item[0].chunk_id),
        )
        results = [
            replace(candidate, rank=rank, reranker_score=score)
            for rank, (candidate, score) in enumerate(ranked[:top_k], start=1)
        ]
        return RerankOutcome(
            results=results,
            latency_ms=(perf_counter() - started) * 1000,
        )


class RerankingEvaluationRetriever:
    """Expose an opt-in evaluation mode after the canonical hybrid retriever."""

    def __init__(
        self,
        delegate: EvaluationRetriever,
        reranker: Reranker,
        candidate_count: int = RERANKER_CANDIDATE_K,
    ) -> None:
        if candidate_count <= 0:
            raise ValueError("candidate_count must be positive")
        self._delegate = delegate
        self._reranker = reranker
        self._candidate_count = candidate_count

    def retrieve(
        self,
        query: str,
        mode: RetrievalMode | str,
        top_k: int,
    ) -> RetrievalResponse:
        mode_name = mode.value if isinstance(mode, RetrievalMode) else mode
        if mode_name != HYBRID_RERANKED_MODE:
            return self._delegate.retrieve(query, mode, top_k)

        candidate_count = max(top_k, self._candidate_count)
        retrieval_started = perf_counter()
        hybrid = self._delegate.retrieve(
            query,
            RetrievalMode.HYBRID,
            candidate_count,
        )
        candidate_retrieval_latency_ms = (
            perf_counter() - retrieval_started
        ) * 1000
        outcome = self._reranker.rerank(query, hybrid.results, top_k)
        return RetrievalResponse(
            requested_mode=RetrievalMode.HYBRID,
            path_used=RetrievalMode.HYBRID,
            reason=(
                "Experimental CrossEncoder reranking over the unchanged hybrid "
                f"top-{candidate_count} candidate set."
            ),
            results=outcome.results,
            final_top_k=top_k,
            dense_candidate_count=hybrid.dense_candidate_count,
            lexical_candidate_count=hybrid.lexical_candidate_count,
            candidate_retrieval_latency_ms=candidate_retrieval_latency_ms,
            reranker_latency_ms=outcome.latency_ms,
        )
