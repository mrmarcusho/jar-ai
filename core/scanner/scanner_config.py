"""
Configuration for the drive scanner.
Defines where to look, where to skip, and classification settings.
"""

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

    paths = [
        home / "Downloads",
        home / "Documents",
        home / "Desktop",
    ]

    if platform.system() == "Darwin":  # macOS
        # iCloud Drive
        icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if icloud.exists():
            paths.append(icloud)

        # Common cloud sync folders
        for cloud_dir in ["Dropbox", "Google Drive", "OneDrive"]:
            cloud_path = home / cloud_dir
            if cloud_path.exists():
                paths.append(cloud_path)

    elif platform.system() == "Windows":
        for cloud_dir in ["Dropbox", "Google Drive", "OneDrive"]:
            cloud_path = home / cloud_dir
            if cloud_path.exists():
                paths.append(cloud_path)

    # Only return paths that actually exist
    return [p for p in paths if p.exists()]


# Directories to ALWAYS skip (performance + privacy)
EXCLUDED_DIRS = {
    ".git",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".Trash",
    "Library",          # macOS system library (except iCloud handled above)
    "Applications",
    "System",
    ".cache",
    ".npm",
    ".pip",
    "site-packages",
}

# Maximum directory depth to scan (prevents runaway recursion)
MAX_SCAN_DEPTH = 5

# Minimum PDF file size to consider (bytes) -- skip tiny/corrupt PDFs
MIN_PDF_SIZE = 10_000  # 10KB

# Maximum PDF file size to consider (bytes) -- skip huge non-statement PDFs
MAX_PDF_SIZE = 50_000_000  # 50MB
