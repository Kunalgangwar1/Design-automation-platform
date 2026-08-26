"""Embedding abstraction and local Sentence Transformers implementation."""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from design_automation.core import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_MODEL,
)
from design_automation.models import DocumentChunk


FloatMatrix = NDArray[np.float32]


class EmbeddingService(Protocol):
    """Minimal contract needed by semantic retrieval."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_texts(self, texts: Sequence[str]) -> FloatMatrix: ...


class SentenceTransformerEmbeddingService:
    """Generate local dense embeddings with a configurable model."""

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        device: str = DEFAULT_EMBEDDING_DEVICE,
    ) -> None:
        # Imported lazily so deterministic ingestion does not load ML libraries.
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name
        self._model = SentenceTransformer(model_name, device=device)
        dimension_getter = getattr(self._model, "get_embedding_dimension", None)
        if dimension_getter is None:
            dimension_getter = self._model.get_sentence_embedding_dimension
        dimension = dimension_getter()
        if dimension is None:
            raise ValueError(f"Embedding dimension is unavailable for {model_name}")
        self._dimension = int(dimension)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: Sequence[str]) -> FloatMatrix:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        embeddings = self._model.encode(
            list(texts),
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=False,
        )
        matrix = np.asarray(embeddings, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != self.dimension:
            raise ValueError("Embedding model returned an unexpected matrix shape")
        return matrix


def chunk_embedding_text(chunk: DocumentChunk) -> str:
    """Add document and section context to the text sent to the embedder."""

    parts: list[str] = []
    title = chunk.metadata.get("title")
    if title:
        parts.append(f"Document: {title}")
    if chunk.section:
        parts.append(f"Section: {chunk.section}")
    parts.append(chunk.text)
    return "\n".join(parts)


def embed_chunks(
    chunks: Sequence[DocumentChunk],
    embedding_service: EmbeddingService,
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
) -> FloatMatrix:
    """Embed chunks in explicit batches while preserving chunk order."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not chunks:
        return np.empty((0, embedding_service.dimension), dtype=np.float32)

    batches: list[FloatMatrix] = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        texts = [chunk_embedding_text(chunk) for chunk in batch]
        embeddings = np.asarray(
            embedding_service.embed_texts(texts),
            dtype=np.float32,
        )
        expected_shape = (len(batch), embedding_service.dimension)
        if embeddings.shape != expected_shape:
            raise ValueError(
                f"Expected embedding shape {expected_shape}, got {embeddings.shape}"
            )
        batches.append(embeddings)
    return np.vstack(batches).astype(np.float32, copy=False)
