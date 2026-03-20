"""Path B orchestrator: redact → Claude extract → restore PII.

Usage:
    python -m core.redaction.pipeline data/statements/eStmt_2023-12-13.pdf
    python -m core.redaction.pipeline data/statements/eStmt_2023-12-13.pdf --profile data/pii_profile.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from core.redaction.pii_profile import build_restore_map, build_token_map, load_profile
from core.redaction.redact_pdf import redact_pdf
from core.redaction.extract_claude import extract_with_claude
from core.redaction.restore import restore_pii

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PROFILE = PROJECT_ROOT / "data" / "pii_profile.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "extraction_output"


def run_path_b(
    pdf_path,
    pii_profile_path=None,
    out_dir=None,
    stem=None,
    save_redacted=False,
    extract_backend="claude",
) -> Path | None:
    """Run the full Path B pipeline: redact → extract → restore → categorize → write.

    Returns the path to the written .structured.json file, or None on failure.
    """
    pdf_path = Path(pdf_path)
    pii_profile_path = Path(pii_profile_path) if pii_profile_path else DEFAULT_PROFILE
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    stem = stem or pdf_path.stem

    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load PII profile
    print(f"Loading PII profile: {pii_profile_path}")
    profile = load_profile(pii_profile_path)

    # 2. Build token maps
    token_map = build_token_map(profile)
    restore_map = build_restore_map(token_map)
    print(f"  {len(token_map)} PII values to redact")

    # 3. Redact PDF
    print(f"Redacting PDF: {pdf_path}")
    t0 = time.monotonic()
    redacted_bytes = redact_pdf(pdf_path, token_map)
    print(f"  Redacted ({time.monotonic() - t0:.1f}s, {len(redacted_bytes):,} bytes)")

    if save_redacted:
        redacted_path = out_dir / f"{stem}.redacted.pdf"
        redacted_path.write_bytes(redacted_bytes)
        print(f"  Saved redacted PDF: {redacted_path}")

    # 4. Extract with LLM
    if extract_backend == "gemini":
        from core.redaction.extract_gemini import extract_with_gemini
        print(f"Sending redacted PDF to Gemini API...")
        t0 = time.monotonic()
        structured = extract_with_gemini(redacted_bytes)
    else:
        print(f"Sending redacted PDF to Claude API...")
        t0 = time.monotonic()
        structured = extract_with_claude(redacted_bytes)
    print(f"  Extraction complete ({time.monotonic() - t0:.1f}s)")

    # 5. Restore PII
    structured = restore_pii(structured, restore_map)
    print(f"  PII restored")

    # 6. Categorize transactions
    from core.categorization.categorizer import CategoryStore, TransactionCategorizer

    taxonomy_path = (
        PROJECT_ROOT / "core" / "categorization" / "categories" / "categories_v0.1.0.json"
    )
    store = CategoryStore(str(taxonomy_path))
    categorizer = TransactionCategorizer(store)

    for txn_list in [structured.get("deposits", []), structured.get("withdrawals", [])]:
        for txn in txn_list:
            result = categorizer.categorize(txn)
            txn["category"] = result["category"]
            txn["subcategory"] = result["subcategory"]

    # 7. Write structured JSON
    structured_path = out_dir / f"{stem}.structured.json"
    structured_path.write_text(json.dumps(structured, indent=4), encoding="utf-8")
    print(f"  Written: {structured_path}")

    return structured_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Path B: PII-redacted Claude extraction")
    parser.add_argument("pdf", help="Path to the PDF bank statement")
    parser.add_argument("--profile", default=None, help="Path to pii_profile.json")
    parser.add_argument("--out-dir", default=None, help="Output directory")
    parser.add_argument("--save-redacted", action="store_true", help="Save the redacted PDF to disk for inspection")
    parser.add_argument("--redact-only", action="store_true", help="Only redact the PDF (skip Claude extraction)")
    parser.add_argument("--backend", default="claude", choices=["claude", "gemini"], help="LLM backend for extraction (default: claude)")
    args = parser.parse_args()

    if args.redact_only:
        # Quick mode: just redact and save, no API call needed
        profile_path = Path(args.profile) if args.profile else DEFAULT_PROFILE
        out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(args.pdf).stem

        profile = load_profile(profile_path)
        token_map = build_token_map(profile)
        print(f"Redacting {args.pdf} ({len(token_map)} PII values)...")
        redacted_bytes = redact_pdf(Path(args.pdf), token_map)
        redacted_path = out_dir / f"{stem}.redacted.pdf"
        redacted_path.write_bytes(redacted_bytes)
        print(f"Done. Open the redacted PDF:\n  open {redacted_path}")
        sys.exit(0)

    result = run_path_b(args.pdf, args.profile, args.out_dir, save_redacted=args.save_redacted, extract_backend=args.backend)
    if result:
        print(f"\nDone. Output: {result}")
    else:
        print("\nPipeline failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
