"""
Preview the redacted text that Jar would send to the cloud for a PDF.

Reproduces the on-device steps of the extraction pipeline (PyMuPDF text
extraction, local OCR for scanned PDFs, then PII redaction) WITHOUT making any
cloud/Claude call. Writes the redacted text to data/redacted_preview/<name>.txt
so you can inspect exactly what leaves your machine.

Usage:
    python scripts/show_redacted.py                 # all PDFs in data/statements/
    python scripts/show_redacted.py Statement201302.pdf [more.pdf ...]
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.extraction_engine.extract import extract_text_with_pymupdf, _ocr_pdf
from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine

DB_PATH = PROJECT_ROOT / "data" / "jar.db"
STATEMENTS_DIR = PROJECT_ROOT / "data" / "statements"
OUT_DIR = PROJECT_ROOT / "data" / "redacted_preview"


def main(argv: list[str]) -> None:
    if argv:
        pdfs = [STATEMENTS_DIR / name for name in argv]
    else:
        pdfs = sorted(STATEMENTS_DIR.glob("*.pdf"))

    conn = sqlite3.connect(str(DB_PATH))
    profile = HouseholdProfile.load(conn)
    engine = RedactionEngine(profile)
    conn.close()

    print(f"Household profile configured: {profile.is_configured()}")
    if not profile.is_configured():
        print("  -> NER-only redaction (no household-specific names/accounts masked)\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for pdf in pdfs:
        if not pdf.exists():
            print(f"SKIP (missing): {pdf.name}")
            continue

        text, is_scanned = extract_text_with_pymupdf(pdf)
        if is_scanned:
            text = _ocr_pdf(pdf) or ""

        redacted = engine.redact(text)
        out_path = OUT_DIR / (pdf.stem + ".txt")
        out_path.write_text(redacted)

        masked = redacted.count("[")  # rough token marker count
        print(f"{pdf.name}: {len(text)} chars -> redacted preview "
              f"({'scanned/OCR' if is_scanned else 'text'}, ~{masked} token markers) "
              f"-> {out_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
