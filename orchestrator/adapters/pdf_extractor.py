# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""PDF text extractor via pdfminer.six with OCR fallback for scanned PDFs."""

import logging
import os

from pdfminer.high_level import extract_text as pdfminer_extract

from config import extractor

_log = logging.getLogger(__name__)


@extractor(".pdf")
def extract_pdf(path: str) -> str:
    text = pdfminer_extract(path)
    if text and len(text.strip()) >= 50:
        return text

    ocr_enabled = os.environ.get("EXTRACTOR_OCR_ENABLED", "true").lower() == "true"
    if not ocr_enabled:
        _log.debug("Scanned PDF detected but OCR disabled: %s", path)
        return text or ""

    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        _log.debug("pdf2image/pytesseract not available for OCR fallback")
        return text or ""

    _log.info("OCR fallback for scanned PDF: %s", path)
    pages = convert_from_path(path)
    ocr_parts = []
    for i, page_img in enumerate(pages):
        page_text = pytesseract.image_to_string(page_img)
        if page_text.strip():
            ocr_parts.append(page_text)
    return "\n".join(ocr_parts)
