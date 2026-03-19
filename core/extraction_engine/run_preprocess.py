"""
Run Docling's DocumentConverter on a BofA checking statement PDF.
Writes raw (unprocessed) markdown and JSON to data/extraction_output/.

Usage:
    python run_preprocess.py                        # default: eStmt_2023-12-13.pdf
    python run_preprocess.py eStmt_2023-02-10.pdf   # specify filename
"""

import sys
import json
from pathlib import Path

from docling.document_converter import DocumentConverter

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def preprocess_pdf(pdf_path, out_dir):
    """Run Docling DocumentConverter on a PDF and write raw markdown + JSON.

    Args:
        pdf_path: Path to the input PDF file.
        out_dir:  Directory to write <stem>.md and <stem>.json into.

    Returns:
        Tuple of (md_path, json_path) for the written files.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    stem = pdf_path.stem

    out_dir.mkdir(parents=True, exist_ok=True)

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))

    # Full markdown export (raw Docling output)
    md_text = result.document.export_to_markdown()
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(md_text, encoding="utf-8")

    # Full document structure as dict (raw Docling output)
    doc_dict = result.document.export_to_dict()
    json_text = json.dumps(doc_dict, indent=2, default=str)
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json_text, encoding="utf-8")

    return md_path, json_path


def main():
    pdf_name = sys.argv[1] if len(sys.argv) > 1 else "eStmt_2023-12-13.pdf"
    stem = Path(pdf_name).stem

    pdf_path = DATA_DIR / "statements" / pdf_name
    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {pdf_path}")
        sys.exit(1)

    print(f"Converting: {pdf_path}")
    print("=" * 60)

    out_dir = DATA_DIR / "extraction_output"
    md_path, json_path = preprocess_pdf(pdf_path, out_dir)
    print(f"Markdown written to {md_path}")
    print(f"JSON written to {json_path}")


if __name__ == "__main__":
    main()
