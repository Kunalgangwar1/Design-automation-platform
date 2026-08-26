"""Small BM25 lexical retriever over deterministic document chunks."""

import re

from rank_bm25 import BM25Okapi

from design_automation.models import (
    DocumentChunk,
    RetrievalOrigin,
    RetrievalProvider,
    RetrievalResult,
)
from design_automation.retrieval.embeddings import chunk_embedding_text


TOKEN_PATTERN = re.compile(
    r"/[A-Za-z0-9_{}.-]+(?:/[A-Za-z0-9_{}.-]+)*"
    r"|[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+"
    r"|[A-Za-z0-9]+"
)
HTTP_ROUTE_PATTERN = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/", re.I)
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "how",
    "is",
    "of",
    "should",
    "the",
    "to",
    "use",
    "what",
    "when",
    "which",
    "with",
}


def tokenize_for_lexical_search(text: str) -> list[str]:
    """Keep technical tokens whole while also indexing their component words."""

    tokens: list[str] = []
    for match in TOKEN_PATTERN.finditer(text):
        token = match.group(0).casefold()
        if token not in STOPWORDS:
            tokens.append(token)
        if token.startswith("/") or "_" in token or "-" in token:
            components = re.findall(r"[a-z0-9]+", token)
            tokens.extend(
                component
                for component in components
                if component != token and component not in STOPWORDS
            )
    return tokens


def _chunk_lexical_text(chunk: DocumentChunk) -> str:
    context = [chunk_embedding_text(chunk)]
    document_type = chunk.metadata.get("document_type")
    if document_type:
        context.append(f"Document type: {document_type}")
    if document_type == "interface":
        context.append("API contract")
    if HTTP_ROUTE_PATTERN.search(chunk.text):
        context.append("API endpoint route")
    return "\n".join(context)


class BM25Retriever:
    """Map BM25 scores back to chunks using stable corpus row positions."""

    def __init__(self, chunks: list[DocumentChunk]) -> None:
        self._chunks = list(chunks)
        corpus = [
            tokenize_for_lexical_search(_chunk_lexical_text(chunk))
            for chunk in self._chunks
        ]
        self._index = BM25Okapi(corpus) if corpus else None

    @property
    def size(self) -> int:
        return len(self._chunks)

    def retrieve(self, query: str, top_k: int) -> list[RetrievalResult]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if self._index is None:
            return []

        scores = self._index.get_scores(tokenize_for_lexical_search(query))
        row_ids = sorted(
            range(self.size),
            key=lambda row_id: (-float(scores[row_id]), row_id),
        )[: min(top_k, self.size)]

        results: list[RetrievalResult] = []
        for rank, row_id in enumerate(row_ids, start=1):
            chunk = self._chunks[row_id]
            results.append(
                RetrievalResult(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    section=chunk.section,
                    source=chunk.metadata.get("source_path", ""),
                    text=chunk.text,
                    similarity_score=None,
                    metadata=dict(chunk.metadata),
                    provider=RetrievalProvider.BM25,
                    rank=rank,
                    provider_score=float(scores[row_id]),
                    lexical_rank=rank,
                    lexical_score=float(scores[row_id]),
                    origin=RetrievalOrigin.LEXICAL,
                )
            )
        return results
