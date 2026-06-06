"""
Filename-based classifier for bank statement PDFs.
Scores filenames on a 0.0-1.0 scale based on financial indicators.

This is intentionally a broad net with a low bar -- the user confirms the final list.
False positives are OK (user rejects them). False negatives are bad (missed statements).
"""

import re
from pathlib import Path


STRONG_INDICATORS = {
    "statement": 0.4,
    "estmt": 0.5,
    "e-statement": 0.5,
    "bank statement": 0.6,
    "account statement": 0.5,
    "checking": 0.4,
    "savings": 0.3,
    "credit card": 0.4,
}

BANK_NAMES = {
    "bank of america": 0.5,
    "bofa": 0.5,
    "chase": 0.4,
    "wells fargo": 0.5,
    "wellsfargo": 0.5,
    "citi": 0.3,
    "citibank": 0.5,
    "td bank": 0.5,
    "tdbank": 0.5,
    "capital one": 0.4,
    "capitalone": 0.4,
    "pnc": 0.3,
    "us bank": 0.4,
    "usbank": 0.4,
    "truist": 0.4,
    "key bank": 0.4,
    "keybank": 0.4,
    "citizens": 0.3,
    "huntington": 0.3,
    "regions": 0.2,
    "fifth third": 0.4,
    "ally": 0.2,
    "ally bank": 0.4,
    "schwab": 0.4,
    "fidelity": 0.3,
    "vanguard": 0.3,
    "merrill": 0.3,
    "morgan stanley": 0.4,
    "goldman": 0.3,
    "hsbc": 0.4,
    "barclays": 0.4,
    "santander": 0.4,
    "m&t bank": 0.4,
}

DATE_PATTERNS = [
    r'\d{4}[-_]\d{2}[-_]\d{2}',    # 2023-02-10
    r'\d{2}[-_]\d{2}[-_]\d{4}',    # 02-10-2023
    r'\d{2}[-_]\d{4}',              # 02-2023
    r'\d{4}[-_]\d{2}',              # 2023-02
    r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[\s_-]?\d{4}',  # Jan2023
]

NEGATIVE_INDICATORS = {
    "resume", "cv", "cover letter", "recipe", "manual", "guide", "tutorial",
    "invoice", "receipt", "tax return", "w-2", "w2", "1099", "contract",
    "lease", "agreement", "insurance policy", "prescription", "medical",
    "homework", "assignment", "syllabus", "presentation", "slides",
}


def score_stem(stem: str) -> float:
    """Score a filename stem (no extension) for bank-statement likelihood.

    Returns:
        Score from 0.0 to 1.0.
        >= 0.3: Worth showing to user as a candidate
        >= 0.5: Likely a bank statement
        >= 0.7: Almost certainly a bank statement
    """
    filename = stem.lower().replace("_", " ").replace("-", " ")
    score = 0.0

    for neg in NEGATIVE_INDICATORS:
        if neg in filename:
            return 0.0

    for indicator, weight in STRONG_INDICATORS.items():
        if indicator in filename:
            score += weight

    for bank, weight in BANK_NAMES.items():
        if bank in filename:
            score += weight

    # Run date patterns on original stem (lowered) to preserve - and _ separators
    original_stem = stem.lower()
    for pattern in DATE_PATTERNS:
        if re.search(pattern, original_stem):
            score += 0.15
            break

    if len(stem) < 30:
        score += 0.05

    return min(score, 1.0)


def score_filename(filepath: Path) -> float:
    """Score a PDF file path for bank-statement likelihood (see score_stem)."""
    return score_stem(filepath.stem)


def detect_banks(stem: str) -> list[str]:
    """Return bank names recognized in a filename stem."""
    filename_lower = stem.lower().replace("_", " ").replace("-", " ")
    return [bank for bank in BANK_NAMES if bank in filename_lower]


def classify_name(name: str, size: int, file_id: str) -> dict:
    """Classify a remote (e.g. Google Drive) PDF by filename only.

    Args:
        name: Full filename including extension.
        size: File size in bytes.
        file_id: Remote identifier (e.g. Drive file id).

    Returns:
        Dict with file_id, filename, score, detected_banks, file_size.
    """
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    return {
        "file_id": file_id,
        "filename": name,
        "score": score_stem(stem),
        "detected_banks": detect_banks(stem),
        "file_size": size,
    }


def classify_pdf(filepath: Path) -> dict:
    """Classify a single local PDF file.

    Returns:
        Dict with filepath, filename, score, detected_banks, file_size.
    """
    return {
        "filepath": filepath,
        "filename": filepath.name,
        "score": score_stem(filepath.stem),
        "detected_banks": detect_banks(filepath.stem),
        "file_size": filepath.stat().st_size,
    }
