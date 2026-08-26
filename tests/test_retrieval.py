"""Tests for batched embeddings, exact FAISS search, and persistence."""

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from design_automation.models import DocumentChunk, RetrievalOrigin, RetrievalProvider
from design_automation.retrieval.embeddings import chunk_embedding_text, embed_chunks
from design_automation.retrieval.retriever import SemanticRetriever
from design_automation.retrieval.vector_store import FaissVectorStore


class FakeEmbeddingService:
    model_name = "fake-embedding-model"
    dimension = 3

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def embed_texts(self, texts: Sequence[str]) -> NDArray[np.float32]:
        self.batch_sizes.append(len(texts))
        vectors = [
            [
                float(text.casefold().count("payment")),
                float(text.casefold().count("inventory")),
                1.0,
            ]
            for text in texts
        ]
        return np.asarray(vectors, dtype=np.float32)


def _chunk(index: int, section: str, text: str) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=f"doc-test-chunk-{index:04d}",
        document_id="doc-test",
        chunk_index=index,
        section=section,
        text=text,
        metadata={
            "title": "Test Document",
            "source_path": "requirements/test.md",
            "document_type": "requirement",
        },
    )


def _chunks() -> list[DocumentChunk]:
    return [
        _chunk(0, "Payments", "Handle payment gateway timeouts."),
        _chunk(1, "Inventory", "Restore inventory after a return."),
        _chunk(2, "Audit", "Record every operation."),
    ]


def test_embedding_batches_have_consistent_dimensions_and_context() -> None:
    chunks = _chunks()
    embedding_service = FakeEmbeddingService()

    embeddings = embed_chunks(chunks, embedding_service, batch_size=2)

    assert embeddings.shape == (3, embedding_service.dimension)
    assert embeddings.dtype == np.float32
    assert embedding_service.batch_sizes == [2, 1]
    assert chunk_embedding_text(chunks[0]).startswith(
        "Document: Test Document\nSection: Payments\n"
    )


def test_faiss_index_size_matches_chunk_mapping() -> None:
    chunks = _chunks()
    embeddings = np.eye(3, dtype=np.float32)

    store = FaissVectorStore.build(chunks, embeddings)

    assert store.size == 3
    assert store.dimension == 3
    assert [chunk.chunk_id for chunk in store.chunks] == [
        chunk.chunk_id for chunk in chunks
    ]


def test_normalized_inner_product_search_is_cosine_similarity() -> None:
    chunks = _chunks()[:2]
    embeddings = np.asarray([[10.0, 0.0], [1.0, 1.0]], dtype=np.float32)
    store = FaissVectorStore.build(chunks, embeddings)

    results = store.search(np.asarray([[3.0, 0.0]], dtype=np.float32), top_k=2)

    assert results[0].chunk_id == chunks[0].chunk_id
    assert results[0].similarity_score == pytest.approx(1.0)
    assert results[1].similarity_score == pytest.approx(2**-0.5)


def test_index_rows_map_back_to_deterministic_result_fields() -> None:
    chunks = _chunks()
    store = FaissVectorStore.build(chunks, np.eye(3, dtype=np.float32))

    result = store.search(np.asarray([[0.0, 1.0, 0.0]], dtype=np.float32), 1)[0]

    assert asdict(result) == {
        "chunk_id": "doc-test-chunk-0001",
        "document_id": "doc-test",
        "section": "Inventory",
        "source": "requirements/test.md",
        "text": "Restore inventory after a return.",
        "similarity_score": pytest.approx(1.0),
        "metadata": chunks[1].metadata,
        "provider": RetrievalProvider.FAISS,
        "rank": 1,
        "provider_score": pytest.approx(1.0),
        "dense_rank": 1,
        "dense_score": pytest.approx(1.0),
        "lexical_rank": None,
        "lexical_score": None,
        "fusion_score": None,
        "origin": RetrievalOrigin.DENSE,
    }


def test_top_k_is_configurable_and_capped_by_index_size() -> None:
    chunks = _chunks()
    store = FaissVectorStore.build(chunks, np.eye(3, dtype=np.float32))
    query = np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)

    assert len(store.search(query, top_k=1)) == 1
    assert len(store.search(query, top_k=10)) == 3
    with pytest.raises(ValueError, match="top_k must be positive"):
        store.search(query, top_k=0)


def test_retriever_embeds_query_and_returns_ranked_results() -> None:
    embedding_service = FakeEmbeddingService()
    retriever = SemanticRetriever.build(_chunks(), embedding_service, batch_size=2)

    results = retriever.retrieve("payment problem", top_k=2)

    assert len(results) == 2
    assert results[0].section == "Payments"
    assert all(result.source == "requirements/test.md" for result in results)


def test_save_and_load_preserve_index_and_chunk_mapping(tmp_path: Path) -> None:
    chunks = _chunks()
    store = FaissVectorStore.build(chunks, np.eye(3, dtype=np.float32))
    index_path = tmp_path / "chunks.faiss"
    mapping_path = tmp_path / "chunks.json"

    store.save(index_path, mapping_path, "fake-embedding-model")
    loaded_store, model_name = FaissVectorStore.load(index_path, mapping_path)
    result = loaded_store.search(
        np.asarray([[0.0, 0.0, 1.0]], dtype=np.float32),
        top_k=1,
    )[0]
    payload = json.loads(mapping_path.read_text(encoding="utf-8"))

    assert model_name == "fake-embedding-model"
    assert loaded_store.size == store.size
    assert loaded_store.dimension == store.dimension
    assert result.chunk_id == chunks[2].chunk_id
    assert payload["chunks"][2]["chunk_id"] == chunks[2].chunk_id
