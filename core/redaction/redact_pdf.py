"""Redact PII from PDF files using PyMuPDF, replacing with tokens."""

from pathlib import Path

import fitz  # PyMuPDF


def redact_pdf(pdf_path: Path, token_map: dict) -> bytes:
    """Redact PII values in a PDF, replacing each with its token placeholder.

    For each page, iterates over (pii_value, token) pairs longest-first
    (the ordering from build_token_map), searches for text matches,
    and applies redaction annotations with the token as replacement text.

    Returns the redacted PDF as bytes. The original file is not modified.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))

    for page in doc:
        for pii_value, token in token_map.items():
            rects = page.search_for(pii_value)
            for rect in rects:
                page.add_redact_annot(
                    rect,
                    text=token,
                    fontsize=0,  # auto-fit
                    fill=(1, 1, 1),  # white background
                )
        page.apply_redactions()

    redacted_bytes = doc.tobytes()
    doc.close()
    return redacted_bytes
