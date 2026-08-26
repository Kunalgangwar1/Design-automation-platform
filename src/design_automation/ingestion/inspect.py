"""Print normalized chunks for human inspection."""

import argparse
from pathlib import Path

from design_automation.core import (
    DATA_DIR,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
)
from design_automation.ingestion.chunker import ChunkingConfig
from design_automation.ingestion.pipeline import ingest_documents


SELECTED_METADATA = (
    "requirement_id",
    "document_id",
    "owner",
    "document_owner",
    "priority",
    "status",
    "review_date",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect deterministic chunks produced from enterprise documents."
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap-chars", type=int, default=DEFAULT_CHUNK_OVERLAP)
    return parser


def main() -> None:
    """Run ingestion and print every chunk with traceability information."""

    args = _parser().parse_args()
    config = ChunkingConfig(
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
    )
    result = ingest_documents(args.data_dir, config)

    print(f"Loaded documents: {len(result.documents)}")
    print(f"Generated chunks: {len(result.chunks)}")
    print(f"Chunk configuration: max={config.max_chars}, overlap={config.overlap_chars}")

    for document in result.documents:
        print("\n" + "=" * 80)
        print(f"Document: {document.title}")
        print(f"Type: {document.document_type.value}")
        print(f"Source: {document.source_path}")
        selected = {
            key: document.metadata[key]
            for key in SELECTED_METADATA
            if key in document.metadata
        }
        print(f"Metadata: {selected or '{}'}")

        document_chunks = [
            chunk for chunk in result.chunks if chunk.document_id == document.document_id
        ]
        for chunk in document_chunks:
            print("\n" + "-" * 80)
            print(f"Chunk ID: {chunk.chunk_id}")
            print(f"Chunk index: {chunk.chunk_index}")
            print(f"Section: {chunk.section or '<none>'}")
            print(f"Size: {len(chunk.text)} characters")
            print("Text:")
            print(chunk.text)


if __name__ == "__main__":
    main()
