#!/usr/bin/env python3
"""Redaction audit — scan redacted output for PII that leaked through.

Runs the real pipeline (PyMuPDF/OCR -> RedactionEngine.redact) over a set of
PDFs, then scans the REDACTED text for PII-shaped strings that survived. If
redaction were perfect, the LEAK DETECTOR would find nothing structured.

This catches *structured* PII (account numbers, SSNs, emails, ZIPs, cards,
phones). It cannot catch leaked free-text names — for those, eyeball the
--show output. Run with an empty profile by default (worst case: Pass 2 only).

Usage:
    python bin/redaction_audit.py                       # audit all test PDFs
    python bin/redaction_audit.py data/test_pdfs/bank   # audit one folder
    python bin/redaction_audit.py <file.pdf> --show     # print leak context
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extraction_engine.extract import extract_text_with_pymupdf, _ocr_pdf
from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine

DEFAULT_DIR = ROOT / "data" / "test_pdfs"

RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
CYAN = "\033[36m"; BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"

# Leak-detector patterns. Each: (name, severity, compiled_regex).
# These run on the REDACTED text — any match is a candidate leak.
LEAK_PATTERNS = [
    ("email",        "HIGH", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("ssn",          "HIGH", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card_grouped", "HIGH", re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}\b")),
    ("grouped_acct", "HIGH", re.compile(r"\b\d{3,}(?:[ -]\d{3,}){2,}\b")),
    ("digit_run_8+", "HIGH", re.compile(r"\b\d{8,}\b")),
    ("zip_plus4",    "MED",  re.compile(r"\b\d{5}-\d{4}\b")),
    ("uk_postcode",  "MED",  re.compile(r"\b[A-Za-z]{1,2}\d[A-Za-z\d]?[ ]?\d[A-Za-z]{2}\b")),
    ("state_zip",    "MED",  re.compile(r"\b(?:A[LKZR]|C[AOT]|DE|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\s+\d{5}\b")),
    ("phone",        "MED",  re.compile(r"(?<!\d)(?:\(\d{3}\)\s*|\d{3}[-.\s])\d{3}[-.\s]\d{4}(?!\d)")),
]

# Tokens look like [PERSON_1]; never count a token as a leak.
TOKEN_RE = re.compile(r"\[[A-Z_]+(?:_\d+)?\]")


def _extract_text(pdf_path: Path) -> str | None:
    text, is_scanned = extract_text_with_pymupdf(pdf_path)
    if is_scanned:
        return _ocr_pdf(pdf_path)
    return text


def _find_leaks(redacted: str) -> list[tuple[str, str, str, str]]:
    """Return (severity, kind, matched_text, context) for each candidate leak."""
    leaks = []
    for kind, sev, pat in LEAK_PATTERNS:
        for m in pat.finditer(redacted):
            s = m.group(0)
            # ignore matches that are inside/are a token
            if TOKEN_RE.fullmatch(s):
                continue
            start = max(0, m.start() - 30)
            end = min(len(redacted), m.end() + 30)
            ctx = redacted[start:end].replace("\n", " ")
            leaks.append((sev, kind, s, ctx))
    return leaks


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit redaction for PII leaks.")
    ap.add_argument("path", nargs="?", default=str(DEFAULT_DIR),
                    help="PDF file or directory (default: data/test_pdfs)")
    ap.add_argument("--show", action="store_true", help="Print leak context lines")
    ap.add_argument("--use-profile", action="store_true",
                    help="Load household profile from data/jar.db (default: empty profile)")
    args = ap.parse_args()

    target = Path(args.path)
    if target.is_file():
        pdfs = [target]
    else:
        pdfs = sorted(target.rglob("*.pdf"))
    if not pdfs:
        print(f"{RED}No PDFs found at {target}{RESET}")
        sys.exit(1)

    profile = HouseholdProfile()
    if args.use_profile:
        import sqlite3
        db = ROOT / "data" / "jar.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            profile = HouseholdProfile.load(conn)
            conn.close()

    print(f"{BOLD}Auditing {len(pdfs)} PDF(s){RESET} "
          f"{DIM}(profile: {'configured' if profile.is_configured() else 'empty'}){RESET}\n")

    total_leaks = 0
    total_high = 0
    files_with_leaks = 0
    errored = []
    by_kind: dict[str, int] = {}

    for pdf in pdfs:
        text = _extract_text(pdf)
        if not text or len(text.strip()) < 50:
            errored.append(pdf.name)
            print(f"  {YELLOW}SKIP{RESET} {pdf.name} {DIM}(no extractable text){RESET}")
            continue

        engine = RedactionEngine(profile)
        redacted = engine.redact(text)
        n_tokens = len(engine._store.all_tokens())
        leaks = _find_leaks(redacted)

        if leaks:
            files_with_leaks += 1
            high = sum(1 for s, *_ in leaks if s == "HIGH")
            total_high += high
            total_leaks += len(leaks)
            for _, kind, *_ in leaks:
                by_kind[kind] = by_kind.get(kind, 0) + 1
            mark = f"{RED}LEAK{RESET}" if high else f"{YELLOW}WARN{RESET}"
            print(f"  {mark} {pdf.name}  {DIM}({n_tokens} redacted, "
                  f"{len(leaks)} suspect: {high} HIGH){RESET}")
            if args.show:
                for sev, kind, s, ctx in leaks:
                    color = RED if sev == "HIGH" else YELLOW
                    print(f"        {color}{sev:4} {kind:13}{RESET} {s!r}")
                    print(f"        {DIM}…{ctx}…{RESET}")
        else:
            print(f"  {GREEN} OK {RESET} {pdf.name}  {DIM}({n_tokens} redacted){RESET}")

    print(f"\n{BOLD}═══ SUMMARY ═══{RESET}")
    print(f"  Files audited:        {len(pdfs) - len(errored)}")
    print(f"  Files with leaks:     {files_with_leaks}")
    print(f"  {RED}HIGH-severity leaks:  {total_high}{RESET}")
    print(f"  Total suspect strings: {total_leaks}")
    if by_kind:
        print(f"  By kind: " + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    if errored:
        print(f"  {YELLOW}Unreadable (need OCR/Tesseract): {len(errored)}{RESET}")

    sys.exit(1 if total_high else 0)


if __name__ == "__main__":
    main()
