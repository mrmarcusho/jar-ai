"""
Test script: Run Docling's DocumentConverter on BofA checking statement PDF.
Prints the full structured output so we can see what Docling extracts.
"""

import sys
import json
from pathlib import Path

from docling.document_converter import DocumentConverter

PDF_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "statements" / "eStmt_2023-12-13.pdf"

def main():
    if not PDF_PATH.exists():
        print(f"ERROR: PDF not found at {PDF_PATH}")
        sys.exit(1)

    print(f"Converting: {PDF_PATH}")
    print("=" * 60)

    out_dir = Path(__file__).resolve().parent.parent.parent / "data" / "extraction_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    converter = DocumentConverter()
    result = converter.convert(str(PDF_PATH))

    # Full markdown export (raw Docling output)
    md_text = result.document.export_to_markdown()
    md_path = out_dir / "eStmt_2023-12-13.md"
    md_path.write_text(md_text, encoding="utf-8")
    print(f"Markdown written to {md_path}")

    # Full document structure as dict (raw Docling output)
    doc_dict = result.document.export_to_dict()
    json_text = json.dumps(doc_dict, indent=2, default=str)
    json_path = out_dir / "eStmt_2023-12-13.json"
    json_path.write_text(json_text, encoding="utf-8")
    print(f"JSON written to {json_path}")

if __name__ == "__main__":
    main()
