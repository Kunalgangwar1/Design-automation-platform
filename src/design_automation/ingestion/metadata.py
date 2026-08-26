"""Explicit metadata extraction for the repository fixtures."""

import re
from pathlib import Path

from design_automation.models import DocumentType


METADATA_FIELDS = {
    "requirement id": "requirement_id",
    "owner": "owner",
    "priority": "priority",
    "status": "status",
    "document owner": "document_owner",
    "last reviewed": "review_date",
    "document id": "document_id",
    "decision owner": "decision_owner",
    "related capability": "related_capability",
    "environment": "environment",
}

DIRECTORY_TYPES = {
    "requirements": DocumentType.REQUIREMENT,
    "architecture": DocumentType.ARCHITECTURE,
    "interfaces": DocumentType.INTERFACE,
    "existing_sdds": DocumentType.EXISTING_SDD,
}

MARKDOWN_TITLE_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
MARKDOWN_METADATA_PATTERN = re.compile(
    r"^\*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)


def extract_title(content: str, path: Path, is_markdown: bool) -> str:
    """Use the first H1 when present, otherwise a readable filename."""

    if is_markdown:
        match = MARKDOWN_TITLE_PATTERN.search(content)
        if match:
            return match.group(1).strip()
    return path.stem.replace("_", " ").replace("-", " ").title()


def extract_markdown_metadata(content: str) -> dict[str, str]:
    """Extract only explicitly supported bold-label metadata fields."""

    metadata: dict[str, str] = {}
    for match in MARKDOWN_METADATA_PATTERN.finditer(content):
        source_key = match.group("key").strip().casefold()
        target_key = METADATA_FIELDS.get(source_key)
        if target_key:
            metadata[target_key] = match.group("value").strip()
    return metadata


def is_recognized_metadata_block(content: str) -> bool:
    """Return whether every non-empty line is recognized front-matter metadata."""

    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return False

    for line in lines:
        match = MARKDOWN_METADATA_PATTERN.fullmatch(line)
        if match is None:
            return False
        if match.group("key").strip().casefold() not in METADATA_FIELDS:
            return False
    return True


def infer_document_type(path: Path, data_root: Path) -> DocumentType:
    """Infer a document category from its first directory beneath data."""

    try:
        relative_path = path.resolve().relative_to(data_root.resolve())
    except ValueError:
        return DocumentType.OTHER

    if len(relative_path.parts) < 2:
        return DocumentType.OTHER
    return DIRECTORY_TYPES.get(relative_path.parts[0], DocumentType.OTHER)
