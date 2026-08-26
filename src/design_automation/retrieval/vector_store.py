"""Exact FAISS vector search with a deterministic chunk mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from numpy.typing import NDArray

from design_automation.models import (
    DocumentChunk,
    RetrievalOrigin,
    RetrievalProvider,
    RetrievalResult,
)


FloatMatrix = NDArray[np.float32]
MAPPING_FORMAT_VERSION = 1


def _as_float32_matrix(vectors: NDArray[Any]) -> FloatMatrix:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2:
        raise ValueError("Vectors must be a two-dimensional matrix")
    return np.ascontiguousarray(matrix)


def _normalized(vectors: NDArray[Any]) -> FloatMatrix:
    matrix = _as_float32_matrix(vectors).copy()
    if matrix.shape[0] and np.any(np.linalg.norm(matrix, axis=1) == 0):
        raise ValueError("Cosine similarity is undefined for zero vectors")
    # On unit-length vectors, inner product is exactly cosine similarity.
    faiss.normalize_L2(matrix)
    return matrix


class FaissVectorStore:
    """Own an IndexFlatIP and its same-position DocumentChunk mapping."""

    def __init__(self, index: faiss.Index, chunks: list[DocumentChunk]) -> None:
        if index.ntotal != len(chunks):
            raise ValueError("FAISS rows and chunk mapping must have the same size")
        self._index = index
        self._chunks = list(chunks)

    @classmethod
    def build(
        cls,
        chunks: list[DocumentChunk],
        embeddings: NDArray[Any],
    ) -> FaissVectorStore:
        matrix = _as_float32_matrix(embeddings)
        if matrix.shape[0] != len(chunks):
            raise ValueError("Each chunk must have exactly one embedding row")
        if matrix.shape[1] == 0:
            raise ValueError("Embedding dimension must be positive")

        normalized_embeddings = _normalized(matrix)
        index = faiss.IndexFlatIP(normalized_embeddings.shape[1])
        if normalized_embeddings.shape[0]:
            index.add(normalized_embeddings)
        return cls(index, chunks)

    @property
    def size(self) -> int:
        return int(self._index.ntotal)

    @property
    def dimension(self) -> int:
        return int(self._index.d)

    @property
    def chunks(self) -> tuple[DocumentChunk, ...]:
        return tuple(self._chunks)

    def search(
        self,
        query_embedding: NDArray[Any],
        top_k: int,
    ) -> list[RetrievalResult]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if self.size == 0:
            return []

        query = _as_float32_matrix(query_embedding)
        if query.shape != (1, self.dimension):
            raise ValueError(
                f"Expected one query vector with dimension {self.dimension}, "
                f"got {query.shape}"
            )
        normalized_query = _normalized(query)
        scores, row_ids = self._index.search(normalized_query, min(top_k, self.size))

        results: list[RetrievalResult] = []
        for rank, (score, row_id) in enumerate(
            zip(scores[0], row_ids[0], strict=True),
            start=1,
        ):
            chunk = self._chunks[int(row_id)]
            dense_score = float(score)
            results.append(
                RetrievalResult(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    section=chunk.section,
                    source=chunk.metadata.get("source_path", ""),
                    text=chunk.text,
                    similarity_score=dense_score,
                    metadata=dict(chunk.metadata),
                    provider=RetrievalProvider.FAISS,
                    rank=rank,
                    provider_score=dense_score,
                    dense_rank=rank,
                    dense_score=dense_score,
                    origin=RetrievalOrigin.DENSE,
                )
            )
        return results

    def save(
        self,
        index_path: Path,
        mapping_path: Path,
        embedding_model: str,
    ) -> None:
        """Persist the binary index and readable row mapping separately."""

        index_path.parent.mkdir(parents=True, exist_ok=True)
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(index_path))

        payload = {
            "format_version": MAPPING_FORMAT_VERSION,
            "embedding_model": embedding_model,
            "dimension": self.dimension,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "chunk_index": chunk.chunk_index,
                    "section": chunk.section,
                    "text": chunk.text,
                    "metadata": chunk.metadata,
                }
                for chunk in self._chunks
            ],
        }
        mapping_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @classmethod
    def load(
        cls,
        index_path: Path,
        mapping_path: Path,
    ) -> tuple[FaissVectorStore, str]:
        """Load an index and restore its row-ordered chunk mapping."""

        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
        if payload.get("format_version") != MAPPING_FORMAT_VERSION:
            raise ValueError("Unsupported chunk mapping format")

        chunks = [
            DocumentChunk(
                chunk_id=item["chunk_id"],
                document_id=item["document_id"],
                chunk_index=item["chunk_index"],
                section=item["section"],
                text=item["text"],
                metadata=dict(item["metadata"]),
            )
            for item in payload["chunks"]
        ]
        index = faiss.read_index(str(index_path))
        if index.d != payload.get("dimension"):
            raise ValueError("FAISS index and chunk mapping dimensions differ")
        store = cls(index, chunks)
        return store, str(payload["embedding_model"])
