"""
Process user-confirmed scanner candidates through the full pipeline:
confirmed PDF -> Docling -> markdown -> bank detection -> parser -> structured JSON
"""

from pathlib import Path

from core.extraction_engine.postprocess.run_postprocess import postprocess_and_structurize


def process_confirmed_candidates(confirmed_paths: list[Path], output_dir: Path) -> list[dict]:
    """Run confirmed PDFs through the full extraction pipeline.

    Args:
        confirmed_paths: List of PDF file paths confirmed by the user.
        output_dir: Directory to write extraction outputs.

    Returns:
        List of result dicts with status per file.
    """
    results = []

    for pdf_path in confirmed_paths:
        stem = pdf_path.stem
        result = {
            "source": str(pdf_path),
            "stem": stem,
            "status": "pending",
            "bank": None,
            "structured_json": None,
            "error": None,
        }

        try:
            # Step 1: Docling conversion (PDF -> markdown + JSON)
            from core.extraction_engine.run_preprocess import preprocess
            md_path, json_path = preprocess(pdf_path, output_dir)

            # Step 2: Post-process + structurize (bank detection + parsing)
            structured_path = postprocess_and_structurize(
                md_path, json_path, output_dir, stem
            )
            if structured_path:
                result["status"] = "success"
                result["structured_json"] = str(structured_path)
            else:
                result["status"] = "unknown_bank"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

        results.append(result)

    return results
