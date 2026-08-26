"""Conservative normalization that retains document structure."""


def normalize_text(text: str) -> str:
    """Normalize line endings, trailing whitespace, and excessive blank lines."""

    normalized_line_endings = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized_line_endings.split("\n")]

    normalized_lines: list[str] = []
    previous_was_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_was_blank:
            continue
        normalized_lines.append("" if is_blank else line)
        previous_was_blank = is_blank

    return "\n".join(normalized_lines).strip()
