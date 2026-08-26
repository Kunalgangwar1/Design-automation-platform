"""Tests for deterministic loading, normalization, and chunking."""

from pathlib import Path

from design_automation.core import DATA_DIR
from design_automation.ingestion.chunker import ChunkingConfig, chunk_document
from design_automation.ingestion.loaders import load_document, load_documents
from design_automation.ingestion.normalization import normalize_text
from design_automation.ingestion.pipeline import ingest_documents
from design_automation.models import Document, DocumentType, SourceType


def _document(content: str, source_type: SourceType = SourceType.MARKDOWN) -> Document:
    return Document(
        document_id="doc-test",
        title="Test Document",
        content=content,
        source_path="requirements/test.md",
        source_type=source_type,
        document_type=DocumentType.REQUIREMENT,
        metadata={"owner": "Test Team", "document_type": "requirement"},
    )


def test_loads_all_repository_documents_in_source_order() -> None:
    documents = load_documents(DATA_DIR)

    assert len(documents) == 4
    assert [document.source_path for document in documents] == [
        "architecture/order_management_system.md",
        "existing_sdds/order_cancellation_sdd.md",
        "interfaces/refund_interfaces.md",
        "requirements/partial_refunds.md",
    ]
    assert all(document.content for document in documents)


def test_extracts_fixture_metadata_and_document_type() -> None:
    document = load_document(
        DATA_DIR / "requirements" / "partial_refunds.md",
        DATA_DIR,
    )

    assert document.title == "Requirement: Item-Level Partial Refunds"
    assert document.document_type is DocumentType.REQUIREMENT
    assert document.metadata["requirement_id"] == "OMS-REF-001"
    assert document.metadata["owner"] == "Customer Operations"
    assert document.metadata["priority"] == "High"
    assert document.metadata["status"] == "Proposed"
    assert document.metadata["document_type"] == "requirement"


def test_extracts_architecture_review_metadata() -> None:
    document = load_document(
        DATA_DIR / "architecture" / "order_management_system.md",
        DATA_DIR,
    )

    assert document.metadata["document_owner"] == "Commerce Architecture"
    assert document.metadata["review_date"] == "2026-07-15"


def test_normalization_preserves_structured_and_technical_text() -> None:
    source = (
        "# API  \r\n\r\n\r\n"
        "- Keep `ORDER_NOT_COMPLETED`  \r\n"
        "- Call `POST /orders/{order_id}/refunds`\r\n"
        "\r\n\r\n"
        "Table: `order_status_history`   \r\n"
    )

    assert normalize_text(source) == (
        "# API\n\n"
        "- Keep `ORDER_NOT_COMPLETED`\n"
        "- Call `POST /orders/{order_id}/refunds`\n\n"
        "Table: `order_status_history`"
    )


def test_plain_text_loader_is_supported(tmp_path: Path) -> None:
    text_dir = tmp_path / "requirements"
    text_dir.mkdir()
    source = text_dir / "support_notes.txt"
    source.write_text("First line\r\n\r\n\r\nSecond line  ", encoding="utf-8")

    document = load_document(source, tmp_path)

    assert document.source_type is SourceType.TEXT
    assert document.document_type is DocumentType.REQUIREMENT
    assert document.title == "Support Notes"
    assert document.content == "First line\n\nSecond line"


def test_chunk_ids_order_and_sections_are_stable() -> None:
    document = _document("# Overview\n\nIntro.\n\n## Rules\n\n- Rule A\n- Rule B")

    chunks = chunk_document(document, ChunkingConfig(max_chars=200, overlap_chars=20))

    assert [chunk.chunk_id for chunk in chunks] == [
        "doc-test-chunk-0000",
        "doc-test-chunk-0001",
    ]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert [chunk.section for chunk in chunks] == ["Overview", "Rules"]
    assert chunks[1].text == "## Rules\n\n- Rule A\n- Rule B"
    assert chunks[1].metadata == document.metadata


def test_metadata_only_document_header_is_not_a_chunk() -> None:
    document = load_document(
        DATA_DIR / "requirements" / "partial_refunds.md",
        DATA_DIR,
    )

    chunks = chunk_document(document)

    assert chunks[0].section == "Business statement"
    assert all(chunk.section != document.title for chunk in chunks)
    assert all(chunk.metadata["title"] == document.title for chunk in chunks)
    assert all(chunk.metadata["requirement_id"] == "OMS-REF-001" for chunk in chunks)
    assert all(chunk.metadata["owner"] == "Customer Operations" for chunk in chunks)


def test_content_in_document_title_section_remains_retrievable() -> None:
    document = _document(
        "# Test Document\n\n"
        "This paragraph is real content.\n\n"
        "## Details\n\nMore content."
    )

    chunks = chunk_document(document)

    assert chunks[0].section == "Test Document"
    assert "This paragraph is real content." in chunks[0].text


def test_oversized_section_is_split_with_heading_and_overlap() -> None:
    repeated_text = " ".join(f"word{index:02d}" for index in range(80))
    document = _document(f"## Large section\n\n{repeated_text}")
    config = ChunkingConfig(max_chars=120, overlap_chars=15)

    chunks = chunk_document(document, config)

    assert len(chunks) > 1
    assert all(chunk.section == "Large section" for chunk in chunks)
    assert all(chunk.text.startswith("## Large section\n\n") for chunk in chunks)
    assert all(len(chunk.text) <= config.max_chars for chunk in chunks)
    first_body = chunks[0].text.removeprefix("## Large section\n\n")
    second_body = chunks[1].text.removeprefix("## Large section\n\n")
    assert set(first_body.split()[-2:]) & set(second_body.split()[:3])


def test_pipeline_returns_documents_and_their_chunks() -> None:
    result = ingest_documents(DATA_DIR)

    assert len(result.documents) == 4
    assert result.chunks
    assert {chunk.document_id for chunk in result.chunks} == {
        document.document_id for document in result.documents
    }
