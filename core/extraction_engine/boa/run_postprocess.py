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
    boa_fix_unicode_escapes,
    boa_fix_missing_co_id,
    merge_spanning_header_columns_md,
    merge_spanning_header_columns_json,
)
from boa_structurize import markdown_to_structured_json

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "extraction_output"


def postprocess_and_structurize(md_path, json_path, out_dir, stem):
    """Post-process raw Docling output and generate structured JSON.

    Args:
        md_path:  Path to the raw markdown file from Docling.
        json_path: Path to the raw JSON file from Docling.
        out_dir:  Directory to write the .structured.json into.
        stem:     Filename stem (e.g. 'eStmt_2023-12-13').

    Returns:
        Path to the written .structured.json file.
    """
    md_path = Path(md_path)
    json_path = Path(json_path)
    out_dir = Path(out_dir)

    # Read raw files and apply unicode fix + ACH CO ID repair
    md_text = md_path.read_text(encoding="utf-8")
    raw_json_text = boa_fix_missing_co_id(boa_fix_unicode_escapes(json_path.read_text(encoding="utf-8")))
    raw_doc_dict = json.loads(raw_json_text)

    # Post-process markdown
    md_text = boa_fix_unicode_escapes(md_text)
    md_text = boa_fix_missing_co_id(md_text)
    md_text = merge_spanning_header_columns_md(md_text)
    md_path.write_text(md_text, encoding="utf-8")

    # Post-process JSON (merge columns for clean output)
    doc_dict = merge_spanning_header_columns_json(raw_doc_dict)  # deepcopies
    json_path.write_text(json.dumps(doc_dict, indent=2, default=str), encoding="utf-8")

    # Generate structured JSON — pass raw Docling doc for spatial table parsing
    structured = markdown_to_structured_json(
        md_text, f"{stem}.pdf", docling_doc=raw_doc_dict
    )
    structured_path = out_dir / f"{stem}.structured.json"
    structured_path.write_text(json.dumps(structured, indent=4), encoding="utf-8")

    return structured_path


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "eStmt_2023-12-13.pdf"
    stem = Path(arg).stem

    md_path = DATA_DIR / f"{stem}.md"
    json_path = DATA_DIR / f"{stem}.json"

    for p in (md_path, json_path):
        if not p.exists():
            print(f"ERROR: {p} not found. Run run_preprocess.py first.")
            sys.exit(1)

    structured_path = postprocess_and_structurize(md_path, json_path, DATA_DIR, stem)
    print(f"Post-processed markdown written to {md_path}")
    print(f"Post-processed JSON written to {json_path}")
    print(f"Structured JSON written to {structured_path}")


if __name__ == "__main__":
    main()
