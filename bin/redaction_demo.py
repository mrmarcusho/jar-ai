#!/usr/bin/env python3
"""Redaction demo — see exactly what JAR sends to the cloud.

Runs the SAME pipeline extract_statement() uses (PyMuPDF text extraction or
local Tesseract OCR → RedactionEngine.redact) and shows the original text next
to the redacted text the cloud LLM would actually receive. Nothing is sent
anywhere — this is purely local.

Usage:
    python bin/redaction_demo.py <path/to/statement.pdf>
    python bin/redaction_demo.py <path/to/statement.pdf> --full   # show entire text, not a preview

The household profile is loaded from data/jar.db, so [ACCT_HOLDER]/[PARTNER]/
[CITY] tokens reflect whatever name you saved via the Privacy Setup modal.
"""
from __future__ import annotations

import argparse
import html as _html
import sqlite3
import sys
import tempfile
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extraction_engine.extract import extract_text_with_pymupdf, _ocr_pdf
from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine

DB_PATH = ROOT / "data" / "jar.db"

# ANSI colors
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _load_profile() -> HouseholdProfile:
    if not DB_PATH.exists():
        print(f"{YELLOW}No database at {DB_PATH} — using an empty profile "
              f"(profile-based name redaction disabled).{RESET}")
        return HouseholdProfile()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return HouseholdProfile.load(conn)
    finally:
        conn.close()


def _extract_text(pdf_path: Path) -> str:
    text, is_scanned = extract_text_with_pymupdf(pdf_path)
    if is_scanned:
        print(f"{YELLOW}No text layer found — running local Tesseract OCR...{RESET}")
        ocr_text = _ocr_pdf(pdf_path)
        if not ocr_text:
            print(f"{RED}OCR could not extract text. Is Tesseract installed "
                  f"(brew install tesseract)?{RESET}")
            sys.exit(1)
        return ocr_text
    return text


def _highlight_redacted(redacted_text: str, tokens: dict[str, str]) -> str:
    """HTML-escape the redacted text and wrap each [TOKEN] in a highlight span."""
    escaped = _html.escape(redacted_text)
    # Longest tokens first so [BANK_ACCOUNT_1] isn't partially matched by a prefix.
    for token in sorted(tokens, key=len, reverse=True):
        esc_token = _html.escape(token)
        escaped = escaped.replace(
            esc_token, f'<mark class="tok">{esc_token}</mark>'
        )
    return escaped


def _highlight_original(original_text: str, tokens: dict[str, str]) -> str:
    """HTML-escape the original text and wrap each redacted PII value in a span."""
    escaped = _html.escape(original_text)
    # Longest values first so substrings don't get wrapped inside a larger match.
    for value in sorted(set(tokens.values()), key=len, reverse=True):
        clean = value.strip()
        if not clean:
            continue
        esc_val = _html.escape(clean)
        if esc_val in escaped:
            escaped = escaped.replace(esc_val, f'<mark class="pii">{esc_val}</mark>')
    return escaped


def _write_html(
    pdf_name: str, original: str, redacted: str, tokens: dict[str, str], out_path: Path
) -> None:
    rows = "\n".join(
        f'<tr><td class="tok-cell">{_html.escape(t)}</td>'
        f'<td class="pii-cell">{_html.escape(v.strip())}</td></tr>'
        for t, v in sorted(tokens.items())
    ) or '<tr><td colspan="2">No PII detected</td></tr>'

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Redaction preview — {_html.escape(pdf_name)}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; background:#0d0d1a;
         color:#e0e0e0; margin:0; padding:24px; }}
  h1 {{ font-size:1.1rem; margin:0 0 4px; }}
  .sub {{ color:#888; font-size:.8rem; margin-bottom:20px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  .panel {{ background:#16162a; border:1px solid #2a2a44; border-radius:10px;
           overflow:hidden; }}
  .panel h2 {{ font-size:.8rem; text-transform:uppercase; letter-spacing:.05em;
              margin:0; padding:10px 14px; border-bottom:1px solid #2a2a44; }}
  .orig h2 {{ color:#7fd1ff; }}
  .red  h2 {{ color:#7CFFA0; }}
  pre {{ margin:0; padding:14px; white-space:pre-wrap; word-break:break-word;
        font-family:"SF Mono",Menlo,monospace; font-size:.72rem; line-height:1.5;
        max-height:70vh; overflow:auto; }}
  mark.pii {{ background:#5a1f1f; color:#ff9d9d; border-radius:3px; padding:0 2px; }}
  mark.tok {{ background:#1f4a2a; color:#7CFFA0; border-radius:3px; padding:0 2px; }}
  .map {{ margin-top:24px; background:#16162a; border:1px solid #2a2a44;
         border-radius:10px; padding:14px; }}
  .map h2 {{ font-size:.8rem; text-transform:uppercase; color:#ffd27f; margin:0 0 10px; }}
  table {{ border-collapse:collapse; width:100%; font-size:.75rem; }}
  td {{ padding:4px 10px; border-bottom:1px solid #232340; }}
  .tok-cell {{ color:#7CFFA0; font-family:"SF Mono",Menlo,monospace; white-space:nowrap; }}
  .pii-cell {{ color:#ff9d9d; }}
</style></head><body>
<h1>Redaction preview — {_html.escape(pdf_name)}</h1>
<div class="sub">Left: original (local only). Right: redacted text sent to the cloud LLM.
  <span style="color:#ff9d9d">red</span> = detected PII,
  <span style="color:#7CFFA0">green</span> = replacement token.</div>
<div class="cols">
  <div class="panel orig"><h2>Original — stays on your machine</h2>
    <pre>{_highlight_original(original, tokens)}</pre></div>
  <div class="panel red"><h2>Redacted — sent to cloud</h2>
    <pre>{_highlight_redacted(redacted, tokens)}</pre></div>
</div>
<div class="map"><h2>Token map — {len(tokens)} values redacted</h2>
  <table>{rows}</table></div>
</body></html>"""
    out_path.write_text(doc, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show original vs. redacted statement text.")
    parser.add_argument("pdf", type=Path, help="Path to a bank statement PDF")
    parser.add_argument("--full", action="store_true", help="Show the entire text, not a preview")
    parser.add_argument("--chars", type=int, default=2000, help="Preview length (default 2000)")
    parser.add_argument("--html", action="store_true",
                        help="Generate a side-by-side HTML view and open it in the browser")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"{RED}File not found: {args.pdf}{RESET}")
        sys.exit(1)

    profile = _load_profile()
    if profile.is_configured():
        print(f"{DIM}Profile: {profile.first_name} {profile.last_name}"
              + (f" / partner {profile.partner_first} {profile.partner_last}"
                 if profile.partner_first else "")
              + (f" / {profile.city}" if profile.city else "") + RESET)
    else:
        print(f"{DIM}Profile: (empty — only Presidio NER + pattern recognizers active){RESET}")

    print(f"{DIM}Extracting text from {args.pdf.name}...{RESET}")
    original = _extract_text(args.pdf)

    print(f"{DIM}Redacting (this loads spaCy on first run, ~2s)...{RESET}\n")
    engine = RedactionEngine(profile)
    redacted = engine.redact(original)
    tokens = engine._store.all_tokens()

    if args.html:
        out_path = Path(tempfile.gettempdir()) / f"jar_redaction_{args.pdf.stem}.html"
        _write_html(args.pdf.name, original, redacted, tokens, out_path)
        print(f"{GREEN}Side-by-side view written to {out_path}{RESET}")
        webbrowser.open(out_path.as_uri())
        return

    limit = None if args.full else args.chars
    orig_show = original if limit is None else original[:limit]
    red_show = redacted if limit is None else redacted[:limit]

    print(f"{BOLD}{CYAN}═══ ORIGINAL (stays on your machine) ═══{RESET}")
    print(orig_show)
    if limit and len(original) > limit:
        print(f"{DIM}... [{len(original) - limit} more chars — use --full to see all]{RESET}")

    print(f"\n{BOLD}{GREEN}═══ REDACTED (what the cloud LLM receives) ═══{RESET}")
    print(red_show)
    if limit and len(redacted) > limit:
        print(f"{DIM}... [truncated — use --full to see all]{RESET}")

    # Token legend — what got replaced and with what
    print(f"\n{BOLD}{YELLOW}═══ TOKEN MAP ({len(tokens)} values redacted) ═══{RESET}")
    if not tokens:
        print(f"{DIM}(no PII detected){RESET}")
    else:
        for token, original_value in sorted(tokens.items()):
            print(f"  {GREEN}{token:<22}{RESET} ← {RED}{original_value}{RESET}")

    # Sanity check: round-trip must restore the original
    restored = engine.detokenize(redacted)
    ok = restored == original
    status = f"{GREEN}✓ exact{RESET}" if ok else f"{YELLOW}~ canonical-form (case may differ){RESET}"
    print(f"\n{DIM}Round-trip detokenize: {status}{RESET}")


if __name__ == "__main__":
    main()
