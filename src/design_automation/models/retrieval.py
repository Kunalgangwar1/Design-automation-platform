"""Models returned by semantic retrieval."""

from dataclasses import dataclass, field
from enum import StrEnum


class RetrievalMode(StrEnum):
    """Available local retrieval strategies."""

    DENSE = "dense"
    LEXICAL = "lexical"
    HYBRID = "hybrid"


class RetrievalOrigin(StrEnum):
    """Local candidate lists that contributed a diagnostic result."""

    DENSE = "dense"
    LEXICAL = "lexical"
    BOTH = "both"


class RetrievalProvider(StrEnum):
    """Provider that produced a retrieval result."""

    FAISS = "faiss"
    BM25 = "bm25"
    RRF = "rrf"


@dataclass(frozen=True)
class RetrievalResult:
    """A ranked chunk with optional dense, lexical, and fusion diagnostics."""

    chunk_id: str
    document_id: str
    section: str | None
    source: str
    text: str
    similarity_score: float | None
    metadata: dict[str, object] = field(default_factory=dict)
    provider: RetrievalProvider = RetrievalProvider.FAISS
    rank: int | None = None
    provider_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    lexical_rank: int | None = None
    lexical_score: float | None = None
    fusion_score: float | None = None
    reranker_score: float | None = None
    origin: RetrievalOrigin = RetrievalOrigin.DENSE


@dataclass(frozen=True)
class RetrievalResponse:
    """Results plus visible candidate settings used for the request."""

    requested_mode: RetrievalMode
    path_used: RetrievalMode
    reason: str
    results: list[RetrievalResult]
    final_top_k: int
    dense_candidate_count: int = 0
    lexical_candidate_count: int = 0
    candidate_retrieval_latency_ms: float | None = None
    reranker_latency_ms: float | None = None
