"""Process user-confirmed scanner candidates through cloud LLM extraction."""

from pathlib import Path

from core.extraction_engine.extract import extract_statement


def process_confirmed_candidates(confirmed_paths: list[Path], db_path: Path) -> list[dict]:
    """Run confirmed PDFs through the cloud extraction pipeline.

    Args:
        confirmed_paths: List of PDF file paths confirmed by the user.
        db_path: Path to the SQLite database.

    Returns:
        List of result dicts with status per file.
    """
    results = []
    for pdf_path in confirmed_paths:
        r = extract_statement(pdf_path, db_path)
        results.append({
            "source": str(pdf_path),
            "stem": pdf_path.stem,
            "status": r.status,
            "txn_count": r.txn_count,
            "total_time": round(r.total_time, 1),
            "error": r.error,
        })
    return results
