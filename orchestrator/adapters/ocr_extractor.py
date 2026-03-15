"""OCR extractor for image files (.jpg, .png) via pytesseract."""

import logging

import pytesseract
from PIL import Image

from config import extractor

_log = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.6


@extractor(".jpg", ".jpeg", ".png")
def extract_ocr(path: str) -> str:
    img = Image.open(path)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    confident_words = []
    for word, conf in zip(data["text"], data["conf"]):
        try:
            if float(conf) >= _MIN_CONFIDENCE * 100 and word.strip():
                confident_words.append(word)
        except (ValueError, TypeError):
            continue
    text = " ".join(confident_words)
    if not text.strip():
        _log.debug("No confident text found in %s, skipping", path)
        return ""
    return text
