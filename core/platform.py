"""Cross-platform utilities for JAR."""

import platform
import webbrowser


def get_platform() -> str:
    """Return normalized platform name: 'macos', 'windows', or 'linux'."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    elif system == "Windows":
        return "windows"
    else:
        return "linux"


def open_browser(url: str):
    """Open URL in the default browser (cross-platform)."""
    webbrowser.open(url)
