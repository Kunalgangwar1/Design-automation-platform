"""End-to-end deterministic ingestion orchestration."""

from dataclasses import dataclass
from pathlib import Path

from design_automation.core import DATA_DIR
from design_automation.ingestion.chunker import ChunkingConfig, chunk_document
from design_automation.ingestion.loaders import load_documents
from design_automation.models import Document, DocumentChunk


@dataclass(frozen=True)
class IngestionResult:
    """Documents and chunks produced by one local ingestion run."""

    documents: list[Document]
    chunks: list[DocumentChunk]


def ingest_documents(
    data_root: Path = DATA_DIR,
    chunking: ChunkingConfig | None = None,
) -> IngestionResult:
    """Load and chunk every supported document beneath a data directory."""

    documents = load_documents(data_root)
    chunks = [
        chunk
        for document in documents
        for chunk in chunk_document(document, chunking)
    ]
    return IngestionResult(documents=documents, chunks=chunks)
