"""
Post-process raw Docling extraction output.

Reads the raw markdown and JSON files produced by test_docling.py,
applies all post-processing fixes, and writes the cleaned versions.
"""

import json
import sys
from pathlib import Path

from postprocess import (
    fix_unicode_escapes,
    merge_spanning_header_columns_md,
    merge_spanning_header_columns_json,
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "extraction_output"


def main():
    md_path = DATA_DIR / "eStmt_2023-12-13.md"
    json_path = DATA_DIR / "eStmt_2023-12-13.json"

    for p in (md_path, json_path):
        if not p.exists():
            print(f"ERROR: {p} not found. Run test_docling.py first.")
            sys.exit(1)

    # Post-process markdown
    md_text = md_path.read_text(encoding="utf-8")
    md_text = fix_unicode_escapes(md_text)
    md_text = merge_spanning_header_columns_md(md_text)
    md_path.write_text(md_text, encoding="utf-8")
    print(f"Post-processed markdown written to {md_path}")

    # Post-process JSON
    doc_dict = json.loads(json_path.read_text(encoding="utf-8"))
    doc_dict = merge_spanning_header_columns_json(doc_dict)
    json_text = fix_unicode_escapes(json.dumps(doc_dict, indent=2, default=str))
    json_path.write_text(json_text, encoding="utf-8")
    print(f"Post-processed JSON written to {json_path}")


if __name__ == "__main__":
    main()
