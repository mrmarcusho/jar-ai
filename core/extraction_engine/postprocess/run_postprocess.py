"""Post-process raw Docling extraction output.

Reads the raw markdown and JSON files produced by run_preprocess.py,
detects the bank format, applies bank-specific fixes, and writes structured JSON.

Usage:
    python -m core.extraction_engine.postprocess.run_postprocess                        # default: eStmt_2023-12-13
    python -m core.extraction_engine.postprocess.run_postprocess eStmt_2023-02-10.pdf   # specify filename
    python -m core.extraction_engine.postprocess.run_postprocess --list-banks            # show supported banks
"""

import json
import sys
from pathlib import Path

from core.extraction_engine.postprocess.registry import detect_bank, list_supported_banks


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "extraction_output"


def postprocess_and_structurize(md_path, json_path, out_dir, stem):
    """Post-process raw Docling output and generate structured JSON.

    This function is completely bank-agnostic. It:
    1. Reads raw Docling output
    2. Detects the bank format
    3. Dispatches to the appropriate bank processor
    4. Writes the structured JSON output

    Args:
        md_path:   Path to the raw markdown file from Docling.
        json_path: Path to the raw JSON file from Docling.
        out_dir:   Directory to write the .structured.json into.
        stem:      Filename stem (e.g. 'eStmt_2023-12-13').

    Returns:
        Path to the written .structured.json file, or None if bank not recognized.
    """
    md_path = Path(md_path)
    json_path = Path(json_path)
    out_dir = Path(out_dir)

    # Read raw files
    md_text = md_path.read_text(encoding="utf-8")
    doc_dict = json.loads(json_path.read_text(encoding="utf-8"))

    # Detect bank format
    processor, confidence = detect_bank(md_text)

    if processor is None:
        print(f"WARNING: Unknown bank format for {stem} (best score: {confidence:.2f})")
        print(f"  This statement could not be matched to any supported bank parser.")
        return None

    if confidence >= 0.7:
        print(f"Detected bank: {processor.bank_name} ({processor.country_code}) — confidence: {confidence:.2f}")
    else:
        print(f"Best guess: {processor.bank_name} ({processor.country_code}) — confidence: {confidence:.2f}")
        response = input(f"  Is this a {processor.bank_name} statement? [Y/n]: ").strip().lower()
        if response in ("n", "no"):
            print(f"  Skipped — user rejected detection.")
            return None

    # Apply bank-specific fixes
    md_text = processor.fix_markdown(md_text)
    doc_dict = processor.fix_json(doc_dict)

    # Write cleaned files back
    md_path.write_text(md_text, encoding="utf-8")
    # Apply generic column merging for clean JSON output (Docling artifact fix).
    # The raw doc_dict (pre-merge) is passed to structurize() because the
    # spatial table parser needs the original multi-column layout.
    from core.extraction_engine.postprocess.common import merge_spanning_header_columns_json
    merged_doc_dict = merge_spanning_header_columns_json(doc_dict)
    json_path.write_text(json.dumps(merged_doc_dict, indent=2, default=str), encoding="utf-8")

    # Generate structured JSON — uses raw (pre-column-merge) doc_dict
    structured = processor.structurize(md_text, f"{stem}.pdf", doc_dict)

    # Run validation
    errors = processor.validate(structured)
    if errors:
        print(f"  Validation warnings:")
        for err in errors:
            print(f"    - {err}")

    # Write structured output
    structured_path = out_dir / f"{stem}.structured.json"
    structured_path.write_text(json.dumps(structured, indent=4), encoding="utf-8")

    return structured_path


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--list-banks":
        banks = list_supported_banks()
        print(f"Supported banks ({len(banks)}):")
        for b in banks:
            print(f"  {b['bank_name']} ({b['country_code']}, {b['region']})")
        sys.exit(0)

    arg = sys.argv[1] if len(sys.argv) > 1 else "eStmt_2023-12-13.pdf"
    stem = Path(arg).stem

    md_path = DATA_DIR / f"{stem}.md"
    json_path = DATA_DIR / f"{stem}.json"

    for p in (md_path, json_path):
        if not p.exists():
            print(f"ERROR: {p} not found. Run run_preprocess.py first.")
            sys.exit(1)

    structured_path = postprocess_and_structurize(md_path, json_path, DATA_DIR, stem)

    if structured_path:
        print(f"Post-processed markdown written to {md_path}")
        print(f"Post-processed JSON written to {json_path}")
        print(f"Structured JSON written to {structured_path}")
    else:
        print(f"Skipped — no parser available for this bank format.")
        sys.exit(2)


if __name__ == "__main__":
    main()
