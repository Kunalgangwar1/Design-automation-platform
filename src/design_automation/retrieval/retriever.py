"""Semantic retrieval orchestration over the local FAISS store."""

from __future__ import annotations

from dataclasses import dataclass

from design_automation.core import DEFAULT_EMBEDDING_BATCH_SIZE, DEFAULT_TOP_K
from design_automation.models import DocumentChunk, RetrievalResult
from design_automation.retrieval.embeddings import EmbeddingService, embed_chunks
from design_automation.retrieval.vector_store import FaissVectorStore


@dataclass(frozen=True)
class SemanticRetriever:
    """Embed queries and translate FAISS rows into retrieval results."""

    embedding_service: EmbeddingService
    vector_store: FaissVectorStore

    @classmethod
    def build(
        cls,
        chunks: list[DocumentChunk],
        embedding_service: EmbeddingService,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
    ) -> SemanticRetriever:
        embeddings = embed_chunks(chunks, embedding_service, batch_size)
        vector_store = FaissVectorStore.build(chunks, embeddings)
        return cls(embedding_service, vector_store)

    def retrieve(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[RetrievalResult]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        query_embedding = self.embedding_service.embed_texts([query])
        return self.vector_store.search(query_embedding, top_k)
