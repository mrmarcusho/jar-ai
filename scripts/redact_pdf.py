#!/usr/bin/env python3
"""
redact_pdf.py — produce TRUE-redacted copies of bank-statement PDFs, driven by
the JAR RedactionEngine (the exact PII detector used at the cloud boundary).

Why this exists
---------------
`RedactionEngine` operates on *text*: it detects PII and maps each value to a
token ([PERSON_1], [BANK_ACCOUNT_3], ...). This script reuses that detection to
redact the *document* and hand back a PDF:

  * Digital PDFs — every page word whose text matches a detected PII value is
    removed with a PyMuPDF redaction annotation. apply_redactions() deletes the
    underlying text from the file (not a cosmetic overlay) and paints a black
    bar; the entity family ([BANK_ACCOUNT] etc.) is drawn in the bar.
  * Scanned PDFs — the page is rasterised at 300 dpi (matching the pipeline's
    OCR), OCR'd with pytesseract image_to_data for word boxes, the PII boxes are
    painted black *onto the pixels*, and the page is replaced by the redacted
    image. The original PII pixels are gone and scanned pages carry no text
    layer to leak. (Best-effort: only words Tesseract re-reads are boxed.)

After writing each file it re-reads the output's text layer and asserts no
detected PII value survives (leak check), then prints a per-file report.

Usage
-----
    python scripts/redact_pdf.py FILE.pdf [FILE2.pdf ...]
    python scripts/redact_pdf.py --out data/redacted_pdf_test FILE.pdf ...

Writes <stem>.redacted.pdf into --out (default data/redacted_pdf_test/).
Nothing is sent to the cloud — this is entirely local.
"""
from __future__ import annotations

import argparse
import io
import re
import sqlite3
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # PyMuPDF

fitz.TOOLS.mupdf_display_errors(False)  # silence xref-recovery noise on slightly-corrupt PDFs

from core.extraction_engine.extract import extract_text_with_pymupdf, _ocr_pdf
from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine

DB_PATH = ROOT / "data" / "jar.db"
DEFAULT_OUT = ROOT / "data" / "redacted_pdf_test"
OCR_DPI = 300  # must match core.extraction_engine.extract._ocr_pdf

_ALNUM = re.compile(r"[^a-z0-9]")
_MIN_PIECE = 3   # ignore pieces shorter than this when locating PII (drops 'a','fl','ho')
_LEAK_MIN = 4    # a surviving piece this long counts as a real leak


def _norm(s: str) -> str:
    return _ALNUM.sub("", s.lower())


def _family(token: str) -> str:
    """'[BANK_ACCOUNT_12]' -> 'BANK_ACCOUNT'."""
    return re.sub(r"_\d+$", "", token.strip("[]"))


def _targets(tokens: dict[str, str]) -> dict[str, str]:
    """Map each normalised PII word-piece -> entity family.

    Detected values are split into whitespace pieces; pieces normalising to
    < _MIN_PIECE chars are dropped so we don't black out every 'a'/'fl'/'ho'.
    """
    piece2fam: dict[str, str] = {}
    for token, value in tokens.items():
        fam = _family(token)
        for piece in re.split(r"\s+", value):
            n = _norm(piece)
            if len(n) >= _MIN_PIECE:
                piece2fam.setdefault(n, fam)
    return piece2fam


def _frags(piece2fam: dict[str, str]) -> list:
    """Digit-bearing pieces (>=4 chars) for substring matching.

    Catches PII fused into a larger token by text/OCR extraction, e.g. the
    address '3275 NW 79TH' surfacing as one word '3275NW79TH', or a card last-4
    glued to its label as 'Checking7099'. Whole-word matching alone misses these.
    """
    return [(p, f) for p, f in piece2fam.items()
            if len(p) >= 4 and any(c.isdigit() for c in p)]


def _match(word_norm: str, piece2fam: dict[str, str], frags: list):
    """Return the entity family for a normalised word, or None."""
    fam = piece2fam.get(word_norm)
    if fam is not None:
        return fam
    for fp, ff in frags:
        if fp in word_norm:
            return ff
    return None


def _redact_digital_page(page, piece2fam, frags) -> list:
    """Add redaction annots for every word matching a PII piece.

    Returns [(rect, family)] so labels can be drawn after apply_redactions()
    (which clears whatever was under the bar).
    """
    labels = []
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        n = _norm(word)
        if not n:
            continue
        fam = _match(n, piece2fam, frags)
        if fam is None:
            continue
        rect = fitz.Rect(x0, y0, x1, y1)
        page.add_redact_annot(rect, fill=(0, 0, 0))
        labels.append((rect, fam))
    return labels


def _draw_labels(page, labels) -> None:
    for rect, fam in labels:
        if rect.width < 16 or rect.height < 5:
            continue  # too small to hold legible text — leave a plain black bar
        size = max(4.0, min(7.0, rect.height - 1.5))
        page.insert_textbox(rect, fam, fontsize=size, fontname="helv",
                            color=(1, 1, 1), align=0)


def _redact_scanned_page_png(page, piece2fam, frags) -> tuple[bytes, int]:
    """Rasterise the page, paint PII word-boxes black onto the pixels, return PNG."""
    from PIL import Image, ImageDraw
    import pytesseract

    pix = page.get_pixmap(dpi=OCR_DPI)
    mode = "RGBA" if pix.alpha else "RGB"
    img = Image.frombytes(mode, [pix.width, pix.height], pix.samples).convert("RGB")
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    draw = ImageDraw.Draw(img)
    n = 0
    for i, word in enumerate(data["text"]):
        if not word.strip() or _match(_norm(word), piece2fam, frags) is None:
            continue
        x, y, w, h = (data["left"][i], data["top"][i],
                      data["width"][i], data["height"][i])
        draw.rectangle([x - 1, y - 1, x + w + 1, y + h + 1], fill=(0, 0, 0))
        n += 1
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), n


def _leak_check(out_pdf: Path, tokens: dict[str, str]) -> list[str]:
    """Return PII values whose significant pieces survive in the output text layer."""
    doc = fitz.open(str(out_pdf))
    wordset = {_norm(w[4]) for page in doc for w in page.get_text("words")}
    doc.close()
    leaks = []
    for token, value in tokens.items():
        for piece in re.split(r"\s+", value):
            n = _norm(piece)
            if len(n) < _LEAK_MIN:
                continue
            if n in wordset or (n.isdigit() and any(n in w for w in wordset)):
                leaks.append(f"{token} <- {value!r}")
                break
    return leaks


def redact_pdf(src: Path, out_dir: Path, profile: HouseholdProfile) -> dict:
    # Fresh engine per file — TokenStore must not bleed across statements.
    engine = RedactionEngine(profile)
    text, scanned = extract_text_with_pymupdf(src)
    if scanned:
        text = _ocr_pdf(src) or ""
    engine.redact(text)
    tokens = engine._store.all_tokens()
    piece2fam = _targets(tokens)
    frags = _frags(piece2fam)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (src.stem + ".redacted.pdf")
    doc = fitz.open(str(src))
    boxes = 0

    if not scanned:
        for page in doc:
            labels = _redact_digital_page(page, piece2fam, frags)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            _draw_labels(page, labels)
            boxes += len(labels)
        doc.save(str(out_path), garbage=4, deflate=True)
        doc.close()
    else:
        out = fitz.open()
        for page in doc:
            png, nb = _redact_scanned_page_png(page, piece2fam, frags)
            boxes += nb
            rect = page.rect
            newp = out.new_page(width=rect.width, height=rect.height)
            newp.insert_image(rect, stream=png)
        out.save(str(out_path), garbage=4, deflate=True)
        out.close()
        doc.close()

    leaks = _leak_check(out_path, tokens)
    return {
        "file": src.name,
        "scanned": scanned,
        "pii_values": len(tokens),
        "boxes": boxes,
        "leaks": leaks,
        "out": out_path,
    }


def _load_profile() -> HouseholdProfile:
    if not DB_PATH.exists():
        return HouseholdProfile()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return HouseholdProfile.load(conn)
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+", type=Path, help="PDF file(s) to redact")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    args = ap.parse_args()

    profile = _load_profile()
    print(f"Profile configured: {profile.is_configured()} "
          f"(third-party test docs rely mainly on Presidio NER + structured sweep)\n")

    results = []
    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"  MISSING: {pdf}")
            continue
        r = redact_pdf(pdf, args.out, profile)
        results.append(r)
        status = "LEAK" if r["leaks"] else "clean"
        kind = "scanned" if r["scanned"] else "digital"
        print(f"  [{status:5}] {r['file'][:54]:54} {kind:7} "
              f"PII={r['pii_values']:>3} boxes={r['boxes']:>4}")
        for leak in r["leaks"]:
            print(f"            ! survived: {leak}")

    print(f"\nWrote {len(results)} redacted PDF(s) to {args.out.relative_to(ROOT)}/")
    leaked = [r for r in results if r["leaks"]]
    if leaked:
        print(f"WARNING: {len(leaked)} file(s) had surviving PII in the text layer.")
    else:
        print("Leak check: no detected PII survived in any output text layer.")


if __name__ == "__main__":
    main()
