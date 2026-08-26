"""Dense embeddings and exact local FAISS retrieval."""

from design_automation.retrieval.bedrock import (
    BedrockConfigurationError,
    BedrockKnowledgeBaseRetriever,
    BedrockRetrievalError,
)
from design_automation.retrieval.embeddings import (
    EmbeddingService,
    SentenceTransformerEmbeddingService,
    chunk_embedding_text,
    embed_chunks,
)
from design_automation.retrieval.coordinator import (
    RetrievalConfig,
    RetrievalCoordinator,
    is_identifier_heavy,
)
from design_automation.retrieval.fusion import reciprocal_rank_fusion
from design_automation.retrieval.lexical import BM25Retriever
from design_automation.retrieval.retriever import SemanticRetriever
from design_automation.retrieval.vector_store import FaissVectorStore

__all__ = [
    "BedrockConfigurationError",
    "BedrockKnowledgeBaseRetriever",
    "BedrockRetrievalError",
    "EmbeddingService",
    "BM25Retriever",
    "FaissVectorStore",
    "RetrievalConfig",
    "RetrievalCoordinator",
    "SemanticRetriever",
    "SentenceTransformerEmbeddingService",
    "chunk_embedding_text",
    "embed_chunks",
    "is_identifier_heavy",
    "reciprocal_rank_fusion",
]
