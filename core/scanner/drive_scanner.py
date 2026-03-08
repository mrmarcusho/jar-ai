"""
Drive scanner for auto-discovering bank statement PDFs.
Crawls specified directories, finds PDFs, scores them using
filename heuristics, and returns a ranked candidate list.
"""

import time
from pathlib import Path
from typing import Callable, Optional

from core.scanner.scanner_config import (
    get_default_scan_paths,
    EXCLUDED_DIRS,
    MAX_SCAN_DEPTH,
    MIN_PDF_SIZE,
    MAX_PDF_SIZE,
)
from core.scanner.filename_classifier import classify_pdf


class ScanProgress:
    """Track and report scan progress."""

    def __init__(self, callback: Optional[Callable] = None):
        self.dirs_scanned = 0
        self.pdfs_found = 0
        self.candidates_found = 0
        self.current_dir = ""
        self.start_time = time.time()
        self.callback = callback

    def update(self, current_dir: str, pdfs_found: int, candidates_found: int):
        self.dirs_scanned += 1
        self.current_dir = current_dir
        self.pdfs_found = pdfs_found
        self.candidates_found = candidates_found
        if self.callback:
            self.callback(self)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time


def scan_directory(
    directory: Path,
    max_depth: int = MAX_SCAN_DEPTH,
    min_score: float = 0.3,
    progress: Optional[ScanProgress] = None,
    _current_depth: int = 0,
) -> list[dict]:
    """Recursively scan a directory for PDF files and classify them.

    Args:
        directory: Root directory to scan.
        max_depth: Maximum recursion depth.
        min_score: Minimum score to include a candidate.
        progress: Optional progress tracker.
        _current_depth: Internal recursion depth counter.

    Returns:
        List of candidate dicts sorted by score descending.
    """
    candidates = []

    if _current_depth > max_depth:
        return candidates

    try:
        entries = list(directory.iterdir())
    except PermissionError:
        return candidates
    except OSError:
        return candidates

    pdfs_in_dir = 0

    for entry in entries:
        try:
            if entry.is_dir():
                if entry.name in EXCLUDED_DIRS or entry.name.startswith("."):
                    continue
                sub_candidates = scan_directory(
                    entry,
                    max_depth=max_depth,
                    min_score=min_score,
                    progress=progress,
                    _current_depth=_current_depth + 1,
                )
                candidates.extend(sub_candidates)

            elif entry.is_file() and entry.suffix.lower() == ".pdf":
                file_size = entry.stat().st_size
                if file_size < MIN_PDF_SIZE or file_size > MAX_PDF_SIZE:
                    continue
                pdfs_in_dir += 1
                result = classify_pdf(entry)
                if result["score"] >= min_score:
                    candidates.append(result)

        except (PermissionError, OSError):
            continue

    if progress:
        progress.update(
            str(directory),
            progress.pdfs_found + pdfs_in_dir,
            len(candidates),
        )

    return candidates


def scan_all_default_paths(
    min_score: float = 0.3,
    progress_callback: Optional[Callable] = None,
) -> list[dict]:
    """Scan all default locations for bank statement PDFs.

    Args:
        min_score: Minimum score threshold.
        progress_callback: Optional callback for progress updates.

    Returns:
        Deduplicated list of candidates sorted by score descending.
    """
    scan_paths = get_default_scan_paths()
    progress = ScanProgress(callback=progress_callback)

    all_candidates = []
    seen_paths = set()

    for scan_path in scan_paths:
        candidates = scan_directory(
            scan_path,
            min_score=min_score,
            progress=progress,
        )
        for candidate in candidates:
            resolved = candidate["filepath"].resolve()
            if resolved not in seen_paths:
                seen_paths.add(resolved)
                all_candidates.append(candidate)

    all_candidates.sort(key=lambda c: (-c["score"], c["filename"]))
    return all_candidates


def format_candidates_for_review(candidates: list[dict]) -> str:
    """Format candidates as a readable table for user review.

    Args:
        candidates: List of candidate dicts from scan.

    Returns:
        Formatted string table.
    """
    if not candidates:
        return "No bank statement candidates found."

    lines = [
        f"Found {len(candidates)} potential bank statement(s):\n",
        f"{'#':<4} {'Score':<7} {'Filename':<50} {'Location'}",
        f"{'─' * 4} {'─' * 7} {'─' * 50} {'─' * 30}",
    ]

    for i, c in enumerate(candidates, 1):
        score_str = f"{c['score']:.2f}"
        filename = c["filename"][:48]
        location = str(c["filepath"].parent)
        if len(location) > 50:
            location = "..." + location[-47:]
        bank_hint = ""
        if c["detected_banks"]:
            bank_hint = f"  [{', '.join(c['detected_banks'])}]"
        lines.append(f"{i:<4} {score_str:<7} {filename:<50} {location}{bank_hint}")

    return "\n".join(lines)
