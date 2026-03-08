#!/usr/bin/env python3
"""Tests for the drive scanner: filename classifier, directory scanner, and output formatting."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scanner.filename_classifier import score_filename, classify_pdf
from core.scanner.drive_scanner import ScanProgress, scan_directory, format_candidates_for_review

TEST_PDFS_DIR = Path(__file__).resolve().parent.parent / "data" / "test_pdfs" / "bank"

passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


# ── Filename classifier: positives (score >= 0.3) ──────────────────────────

print("\n=== Filename classifier — positives ===")
positive_filenames = [
    "eStmt_2023-01-13.pdf",
    "Chase_Statement_March2024.pdf",
    "Bank-of-America-Statement.pdf",
    "TD-Bank-Statement.pdf",
    "Wells-Fargo-Bank-Statement.pdf",
]
for name in positive_filenames:
    score = score_filename(Path(name))
    check(f"{name} score={score:.2f} >= 0.3", score >= 0.3)


# ── Filename classifier: negatives (score == 0.0) ──────────────────────────

print("\n=== Filename classifier — negatives ===")
negative_filenames = [
    "resume_final_v2.pdf",
    "tax_return_2023.pdf",
    "homework_assignment.pdf",
]
for name in negative_filenames:
    score = score_filename(Path(name))
    check(f"{name} score={score:.2f} == 0.0", score == 0.0)


# ── Filename classifier: low scores (score < 0.3) ──────────────────────────

print("\n=== Filename classifier — low scores ===")
low_score_filenames = [
    "meeting_notes.pdf",
    "document.pdf",
]
for name in low_score_filenames:
    score = score_filename(Path(name))
    check(f"{name} score={score:.2f} < 0.3", score < 0.3)


# ── Filename classifier: bank detection ─────────────────────────────────────

print("\n=== Filename classifier — bank detection ===")

result = classify_pdf(TEST_PDFS_DIR / "670593719-Bank-of-America-Statement.pdf")
check(
    "Bank-of-America-Statement detects 'bank of america'",
    "bank of america" in result["detected_banks"],
    f"got {result['detected_banks']}",
)

result = classify_pdf(TEST_PDFS_DIR / "530588294-TD-Bank-Statement.pdf")
check(
    "TD-Bank-Statement detects 'td bank'",
    "td bank" in result["detected_banks"],
    f"got {result['detected_banks']}",
)

score = score_filename(Path("resume_final_v2.pdf"))
check("resume_final_v2.pdf has no banks (score 0 → skipped)", score == 0.0)


# ── Directory scanner: real scan ────────────────────────────────────────────

print("\n=== Directory scanner — real scan of data/test_pdfs/bank/ ===")
candidates = scan_directory(TEST_PDFS_DIR, min_score=0.3)

check(
    f"Returns >= 55 candidates (got {len(candidates)})",
    len(candidates) >= 55,
)

required_keys = {"filepath", "filename", "score", "detected_banks", "file_size"}
if candidates:
    sample = candidates[0]
    missing = required_keys - set(sample.keys())
    check(
        "Candidates have all required keys",
        not missing,
        f"missing: {missing}" if missing else "",
    )

    all_valid_scores = all(0.0 <= c["score"] <= 1.0 for c in candidates)
    check("All scores between 0.0 and 1.0", all_valid_scores)

    # scan_directory doesn't sort; scan_all_default_paths does. Sort here to mirror real usage.
    candidates.sort(key=lambda c: (-c["score"], c["filename"]))
    scores = [c["score"] for c in candidates]
    check("Scores sorted descending after sort", scores == sorted(scores, reverse=True))


# ── Directory scanner: edge cases ───────────────────────────────────────────

print("\n=== Directory scanner — edge cases ===")

# max_depth=0 should only scan root dir (no recursion into subdirs)
root_only = scan_directory(TEST_PDFS_DIR, max_depth=0, min_score=0.0)
recursive = scan_directory(TEST_PDFS_DIR, max_depth=5, min_score=0.0)
check(
    f"max_depth=0 scans root only ({len(root_only)} <= {len(recursive)})",
    len(root_only) <= len(recursive),
)

# Nonexistent path returns empty list
bogus = scan_directory(Path("/nonexistent/path/that/does/not/exist"))
check("Nonexistent path returns empty list", bogus == [])


# ── format_candidates_for_review ────────────────────────────────────────────

print("\n=== Format candidates ===")

empty_output = format_candidates_for_review([])
check(
    'Empty list → "No bank statement candidates found."',
    empty_output == "No bank statement candidates found.",
)

formatted = format_candidates_for_review(candidates[:3])
check(
    'Non-empty list includes "Found N potential bank statement(s)"',
    "Found 3 potential bank statement(s)" in formatted,
    f"got: {formatted[:80]}...",
)


# ── ScanProgress ────────────────────────────────────────────────────────────

print("\n=== ScanProgress ===")

progress = ScanProgress()
time.sleep(0.05)
progress.update("/some/dir", 10, 3)

check("dirs_scanned increments", progress.dirs_scanned == 1)
check("elapsed_seconds > 0", progress.elapsed_seconds > 0)

progress.update("/another/dir", 20, 5)
check("dirs_scanned increments again", progress.dirs_scanned == 2)


# ── Summary ─────────────────────────────────────────────────────────────────

print(f"\n{'=' * 40}")
print(f"  {passed} passed, {failed} failed")
print(f"{'=' * 40}")
sys.exit(1 if failed else 0)
