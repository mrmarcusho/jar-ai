"""
Configuration for the drive scanner.
Defines where to look, where to skip, and classification settings.
"""

import os
import platform
from pathlib import Path


def get_default_scan_paths() -> list[Path]:
    """Return default directories to scan based on OS.

    Focus on locations where users typically store or download bank statements:
    - Downloads folder (most common -- banks email or download PDFs here)
    - Documents folder
    - Desktop
    - Cloud sync folders (iCloud Drive, Dropbox, Google Drive, OneDrive)
    """
    home = Path.home()
    system = platform.system()

    paths = [
        home / "Downloads",
        home / "Documents",
        home / "Desktop",
    ]

    # macOS-only: iCloud Drive
    if system == "Darwin":
        icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if icloud.exists():
            paths.append(icloud)

    # Windows-only: OneDrive variants
    if system == "Windows":
        for onedrive_name in ["OneDrive", "OneDrive - Personal"]:
            od = home / onedrive_name
            if od.exists():
                paths.append(od)
                break

    # Linux-only: XDG directories (if different from defaults)
    if system == "Linux":
        xdg_docs = Path(os.environ.get("XDG_DOCUMENTS_DIR", str(home / "Documents")))
        xdg_downloads = Path(os.environ.get("XDG_DOWNLOAD_DIR", str(home / "Downloads")))
        for xdg_path in [xdg_docs, xdg_downloads]:
            if xdg_path.exists() and xdg_path not in paths:
                paths.append(xdg_path)

    # Cloud folders (cross-platform)
    for cloud_dir in ["Dropbox", "Google Drive", "Google Drive Streaming", "Box"]:
        cloud_path = home / cloud_dir
        if cloud_path.exists() and cloud_path not in paths:
            paths.append(cloud_path)

    # Only return paths that actually exist
    return [p for p in paths if p.exists()]


# Common exclusions (backward-compatible constant)
EXCLUDED_DIRS = {
    ".git",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".Trash",
    ".cache",
    ".npm",
    ".pip",
    "site-packages",
}


def get_excluded_dirs() -> set:
    """Return directories to skip during scanning (platform-aware)."""
    excluded = set(EXCLUDED_DIRS)
    system = platform.system()

    if system == "Darwin":
        excluded.update({"Library", "Applications", "System"})
    elif system == "Windows":
        excluded.update({
            "AppData", "ProgramData", "$Recycle.Bin",
            "Windows", "Program Files", "Program Files (x86)",
        })
    elif system == "Linux":
        excluded.update({".local", ".config", "snap", ".snap"})

    return excluded


# Maximum directory depth to scan (prevents runaway recursion)
MAX_SCAN_DEPTH = 5

# Minimum PDF file size to consider (bytes) -- skip tiny/corrupt PDFs
MIN_PDF_SIZE = 10_000  # 10KB

# Maximum PDF file size to consider (bytes) -- skip huge non-statement PDFs
MAX_PDF_SIZE = 50_000_000  # 50MB
