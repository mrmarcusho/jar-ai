#!/usr/bin/env bash
# Jar Finance — Cross-Platform Installer (macOS + Linux)
# Run: bash bin/install.sh

set -e

echo "=============================="
echo "  Jar — Installation"
echo "=============================="
echo ""

# ── Detect OS ────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin*) PLATFORM="macos" ;;
    Linux*)  PLATFORM="linux" ;;
    *)       echo "Unsupported OS: $OS. Use install.ps1 on Windows."; exit 1 ;;
esac
echo "Platform: $PLATFORM"

# ── Find project root (directory containing this script's parent) ─
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"
echo "Project:  $PROJECT_ROOT"

# ── Check Python 3.9+ ───────────────────────────────────────────
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
        ver=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo ""
    echo "Python 3.9+ not found."
    if [ "$PLATFORM" = "macos" ]; then
        echo "Install with:  brew install python3"
        echo "  or download: https://www.python.org/downloads/"
    else
        echo "Install with:  sudo apt install python3 python3-pip python3-venv"
        echo "  or:          sudo dnf install python3 python3-pip"
    fi
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
echo "Python:   $PYTHON_VERSION ($PYTHON_CMD)"

# ── Linux: check for venv package ────────────────────────────────
if [ "$PLATFORM" = "linux" ]; then
    if ! "$PYTHON_CMD" -m venv --help &> /dev/null; then
        echo ""
        echo "Python venv module not found. Install it:"
        echo "  sudo apt install python3-venv"
        exit 1
    fi
fi

# ── Create virtual environment ───────────────────────────────────
echo ""
if [ -d "venv" ]; then
    echo "Virtual environment already exists."
else
    echo "Creating virtual environment..."
    "$PYTHON_CMD" -m venv venv
fi
source venv/bin/activate

# ── Install dependencies ─────────────────────────────────────────
echo "Installing dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

echo "Dependencies installed."

# ── Initialize database ─────────────────────────────────────────
echo ""
echo "Initializing database..."
mkdir -p data
python -c "
import sqlite3, os
db_path = os.path.join('data', 'jar.db')
schema_path = os.path.join('core', 'database', 'schema.sql')
if os.path.exists(db_path):
    # Run schema anyway to pick up new tables (IF NOT EXISTS is safe)
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print('Database updated (existing data preserved)')
elif os.path.exists(schema_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print('Database initialized')
else:
    print('Warning: schema.sql not found, skipping DB init')
"

# ── Create launcher ──────────────────────────────────────────────
echo ""
if [ "$PLATFORM" = "macos" ]; then
    LAUNCHER="$HOME/Desktop/Start-Jar.command"
    cat > "$LAUNCHER" << LAUNCH_EOF
#!/bin/bash
cd "$PROJECT_ROOT"
source venv/bin/activate
echo "Starting Jar at http://localhost:5000"
open http://localhost:5000
python app/server.py
LAUNCH_EOF
    chmod +x "$LAUNCHER"
    echo "Launcher created: $LAUNCHER"
else
    LAUNCHER="$PROJECT_ROOT/launch_jar.sh"
    cat > "$LAUNCHER" << LAUNCH_EOF
#!/bin/bash
cd "$PROJECT_ROOT"
source venv/bin/activate
echo "Starting Jar at http://localhost:5000"
xdg-open http://localhost:5000 2>/dev/null || echo "Open http://localhost:5000 in your browser"
python app/server.py
LAUNCH_EOF
    chmod +x "$LAUNCHER"
    echo "Launcher created: $LAUNCHER"
fi

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo "=============================="
echo "  Jar installed successfully!"
echo "=============================="
echo ""
if [ "$PLATFORM" = "macos" ]; then
    echo "  To start: double-click Start-Jar on your Desktop"
    echo "  Or run:   bash ~/Desktop/Start-Jar.command"
else
    echo "  To start: ./launch_jar.sh"
fi
echo ""
echo "  Set ANTHROPIC_API_KEY in .env for Claude-powered chat and extraction."
echo ""
