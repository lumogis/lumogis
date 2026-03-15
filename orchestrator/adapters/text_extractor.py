"""Plain text extractor for .txt and .md files."""

from config import extractor


@extractor(".txt", ".md")
def extract_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()
