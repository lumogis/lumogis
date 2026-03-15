"""DOCX text extractor via python-docx."""

from docx import Document

from config import extractor


@extractor(".docx")
def extract_docx(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
