"""
CLI interface for testing the drive scanner.

Usage:
    python -m core.scanner.scanner_cli                    # scan default paths
    python -m core.scanner.scanner_cli --path ~/Downloads  # scan specific path
    python -m core.scanner.scanner_cli --min-score 0.5    # higher threshold
    python -m core.scanner.scanner_cli --verbose          # show progress
"""

import argparse
import sys
from pathlib import Path

from core.scanner.drive_scanner import (
    scan_all_default_paths,
    scan_directory,
    format_candidates_for_review,
    ScanProgress,
)
from core.scanner.scanner_config import get_default_scan_paths


def progress_printer(progress):
    """Print scan progress to stdout."""
    sys.stdout.write(
        f"\rScanning... {progress.dirs_scanned} dirs | "
        f"{progress.pdfs_found} PDFs found | "
        f"{progress.candidates_found} candidates | "
        f"{progress.elapsed_seconds:.1f}s"
    )
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="Scan drives for bank statement PDFs")
    parser.add_argument(
        "--path", type=str,
        help="Specific directory to scan (default: all standard locations)",
    )
    parser.add_argument(
        "--min-score", type=float, default=0.3,
        help="Minimum score threshold (default: 0.3)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show scan progress",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON",
    )
    args = parser.parse_args()

    callback = progress_printer if args.verbose else None

    if args.path:
        scan_path = Path(args.path).expanduser()
        if not scan_path.exists():
            print(f"ERROR: {scan_path} does not exist.")
            sys.exit(1)

        progress = ScanProgress(callback=callback)
        candidates = scan_directory(scan_path, min_score=args.min_score, progress=progress)
        candidates.sort(key=lambda c: (-c["score"], c["filename"]))
    else:
        default_paths = get_default_scan_paths()
        print(f"Scanning: {', '.join(str(p) for p in default_paths)}\n")
        candidates = scan_all_default_paths(
            min_score=args.min_score, progress_callback=callback,
        )

    if args.verbose:
        print()  # newline after progress line

    if args.json:
        import json
        json_candidates = [
            {**c, "filepath": str(c["filepath"])}
            for c in candidates
        ]
        print(json.dumps(json_candidates, indent=2))
    else:
        print(format_candidates_for_review(candidates))

        if candidates:
            print(f"\nTo process these statements, confirm which ones are bank statements")
            print(f"and run them through the extraction pipeline.")


if __name__ == "__main__":
    main()
