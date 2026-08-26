"""Models returned by semantic retrieval."""

from dataclasses import dataclass, field
from enum import StrEnum


class RetrievalMode(StrEnum):
    """Available canonical and diagnostic retrieval strategies."""

    FAISS = "faiss"
    BEDROCK = "bedrock"
    LEXICAL = "lexical"
    FALLBACK = "fallback"
    HYBRID = "hybrid"


class RetrievalOrigin(StrEnum):
    """Local candidate lists that contributed a diagnostic result."""

    DENSE = "dense"
    BEDROCK = "bedrock"
    LEXICAL = "lexical"
    BOTH = "both"


class RetrievalProvider(StrEnum):
    """Provider that produced a retrieval result."""

    FAISS = "faiss"
    BEDROCK_KB = "bedrock_kb"
    BM25 = "bm25"
    DIAGNOSTIC_RRF = "diagnostic_rrf"


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
    origin: RetrievalOrigin = RetrievalOrigin.DENSE


@dataclass(frozen=True)
class RetrievalResponse:
    """Results plus the observable decision made by retrieval orchestration."""

    requested_mode: RetrievalMode
    path_used: RetrievalMode
    reason: str
    results: list[RetrievalResult]
    fallback_triggered: bool = False
    faiss_top_score: float | None = None
    fallback_threshold: float | None = None
    final_provider: RetrievalProvider | None = None

    @property
    def dense_top_score(self) -> float | None:
        """Backward-compatible name for the local FAISS top score."""

        return self.faiss_top_score
