"""Local Markdown and text document loaders."""

from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

from design_automation.ingestion.metadata import (
    extract_markdown_metadata,
    extract_title,
    infer_document_type,
)
from design_automation.ingestion.normalization import normalize_text
from design_automation.models import Document, SourceType


Loader = Callable[[Path, Path], Document]


def _relative_source_path(path: Path, data_root: Path) -> str:
    try:
        return path.resolve().relative_to(data_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _stable_document_id(source_path: str) -> str:
    digest = sha256(source_path.encode("utf-8")).hexdigest()[:12]
    return f"doc-{digest}"


def _load_local_file(path: Path, data_root: Path, source_type: SourceType) -> Document:
    content = normalize_text(path.read_text(encoding="utf-8"))
    is_markdown = source_type is SourceType.MARKDOWN
    document_type = infer_document_type(path, data_root)
    source_path = _relative_source_path(path, data_root)
    title = extract_title(content, path, is_markdown)
    metadata = extract_markdown_metadata(content) if is_markdown else {}
    metadata.update(
        {
            "title": title,
            "source_path": source_path,
            "source_type": source_type.value,
            "document_type": document_type.value,
        }
    )

    return Document(
        document_id=_stable_document_id(source_path),
        title=title,
        content=content,
        source_path=source_path,
        source_type=source_type,
        document_type=document_type,
        metadata=metadata,
    )


def load_markdown(path: Path, data_root: Path) -> Document:
    """Load and normalize one UTF-8 Markdown file."""

    return _load_local_file(path, data_root, SourceType.MARKDOWN)


def load_text(path: Path, data_root: Path) -> Document:
    """Load and normalize one UTF-8 plain-text file."""

    return _load_local_file(path, data_root, SourceType.TEXT)


# Adding a local format requires a loader function and one registry entry.
LOADERS_BY_EXTENSION: dict[str, Loader] = {
    ".md": load_markdown,
    ".markdown": load_markdown,
    ".txt": load_text,
}


def supported_file(path: Path) -> bool:
    """Return whether a loader is registered for the file extension."""

    return path.is_file() and path.suffix.casefold() in LOADERS_BY_EXTENSION


def load_document(path: Path, data_root: Path) -> Document:
    """Load a supported file or raise a clear format error."""

    loader = LOADERS_BY_EXTENSION.get(path.suffix.casefold())
    if loader is None:
        raise ValueError(f"Unsupported document format: {path.suffix or '<none>'}")
    return loader(path, data_root)


def load_documents(data_root: Path) -> list[Document]:
    """Load supported files recursively in stable source-path order."""

    paths = sorted(
        (path for path in data_root.rglob("*") if supported_file(path)),
        key=lambda path: path.relative_to(data_root).as_posix(),
    )
    return [load_document(path, data_root) for path in paths]
