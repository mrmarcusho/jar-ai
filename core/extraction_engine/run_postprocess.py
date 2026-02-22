"""
Post-process raw Docling extraction output.

Reads the raw markdown and JSON files produced by run_preprocess.py,
applies all post-processing fixes, and writes the cleaned versions.

Usage:
    python run_postprocess.py                        # default: eStmt_2023-12-13
    python run_postprocess.py eStmt_2023-02-10.pdf   # specify filename
"""

import json
import sys
from pathlib import Path

from postprocess import (
    fix_unicode_escapes,
    merge_spanning_header_columns_md,
    merge_spanning_header_columns_json,
)
from structurize import markdown_to_structured_json

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "extraction_output"


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "eStmt_2023-12-13.pdf"
    stem = Path(arg).stem

    md_path = DATA_DIR / f"{stem}.md"
    json_path = DATA_DIR / f"{stem}.json"

    for p in (md_path, json_path):
        if not p.exists():
            print(f"ERROR: {p} not found. Run run_preprocess.py first.")
            sys.exit(1)

    # Read raw files and apply unicode fix
    md_text = md_path.read_text(encoding="utf-8")
    raw_json_text = fix_unicode_escapes(json_path.read_text(encoding="utf-8"))
    raw_doc_dict = json.loads(raw_json_text)

    # Post-process markdown
    md_text = fix_unicode_escapes(md_text)
    md_text = merge_spanning_header_columns_md(md_text)
    md_path.write_text(md_text, encoding="utf-8")
    print(f"Post-processed markdown written to {md_path}")

    # Post-process JSON (merge columns for clean output)
    doc_dict = merge_spanning_header_columns_json(raw_doc_dict)  # deepcopies
    json_path.write_text(json.dumps(doc_dict, indent=2, default=str), encoding="utf-8")
    print(f"Post-processed JSON written to {json_path}")

    # Generate structured JSON — pass raw Docling doc for spatial table parsing
    structured = markdown_to_structured_json(
        md_text, f"{stem}.pdf", docling_doc=raw_doc_dict
    )
    structured_path = DATA_DIR / f"{stem}.structured.json"
    structured_path.write_text(json.dumps(structured, indent=4), encoding="utf-8")
    print(f"Structured JSON written to {structured_path}")


if __name__ == "__main__":
    main()
