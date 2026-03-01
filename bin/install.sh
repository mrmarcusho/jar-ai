#!/bin/bash
# Jar Finance — Mac Setup
# Run: bash install.sh

set -e

echo "🫙 Installing Jar..."

# 1. Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Add Homebrew to PATH for Apple Silicon Macs
    if [ -f "/opt/homebrew/bin/brew" ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
        echo '' >> ~/.zprofile
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
    fi
fi

# 2. Check for Git
if ! command -v git &> /dev/null; then
    echo "Installing Git..."
    brew install git
fi

# 3. Install system libraries needed to compile Python with full module support
echo "Installing build dependencies..."
brew install xz openssl readline sqlite3 zlib 2>/dev/null || true

# 4. Install pyenv for isolated Python version control
if ! command -v pyenv &> /dev/null; then
    echo "Installing pyenv..."
    brew install pyenv

    # Add pyenv to shell (won't affect system Python)
    echo '' >> ~/.zshrc
    echo '# pyenv (installed by Jar)' >> ~/.zshrc
    echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
    echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
    echo 'eval "$(pyenv init -)"' >> ~/.zshrc

    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"
fi

# 5. Install Python 3.11.9 in ~/.pyenv/ (does NOT touch system Python)
# Verify existing install has lzma support; rebuild if missing
if ~/.pyenv/versions/3.11.9/bin/python3 -c "import lzma" 2>/dev/null; then
    echo "Python 3.11.9 already installed with full module support."
else
    echo "Installing Python 3.11.9 (isolated in ~/.pyenv/)..."
    pyenv uninstall -f 3.11.9 2>/dev/null || true
    pyenv install 3.11.9
fi

# 6. Clone repo (or pull if exists)
if [ -d "$HOME/JAR" ]; then
    echo "Updating existing installation..."
    cd "$HOME/JAR"
    git pull
else
    echo "Cloning Jar..."
    git clone https://github.com/dhodtong365/jar-ai.git "$HOME/JAR"
    cd "$HOME/JAR"
fi

# 7. Always create fresh virtual environment with correct Python version
echo "Setting up Python environment..."
rm -rf "$HOME/JAR/venv"
~/.pyenv/versions/3.11.9/bin/python3 -m venv "$HOME/JAR/venv"
source "$HOME/JAR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$HOME/JAR/requirements.txt"

# 8. Initialize database
echo "Initializing database..."
mkdir -p "$HOME/JAR/data"
python -c "
import sqlite3, os
db_path = os.path.expanduser('~/JAR/data/jar.db')
schema_path = os.path.expanduser('~/JAR/core/database/schema.sql')
if os.path.exists(schema_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print('Database initialized at', db_path)
else:
    print('Warning: schema.sql not found, skipping DB init')
"

# 9. Install Ollama for local LLM (macOS uses brew cask, not the Linux script)
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    brew install --cask ollama
    
    # Launch Ollama app once to register the CLI and start the service
    open -a Ollama
    echo "Waiting for Ollama to start..."
    sleep 5
fi

# 10. Pull the LLM model
echo "Downloading Qwen2.5 3B (first run may take a few minutes)..."
ollama pull qwen2.5:3b

# 11. Create Desktop launcher
cat > "$HOME/Desktop/Start-Jar.command" << 'EOF'
#!/bin/bash
cd "$HOME/JAR"
source venv/bin/activate

# Start Ollama if not running
if ! pgrep -x "ollama" > /dev/null; then
    open -a Ollama
    sleep 3
fi

echo "🫙 Starting Jar at http://localhost:5000"
open http://localhost:5000
python app/server.py
EOF
chmod +x "$HOME/Desktop/Start-Jar.command"

echo ""
echo "============================================"
echo "  ✅ Jar installed successfully!"
echo "============================================"
echo ""
echo "  To start: double-click Start-Jar on your Desktop"
echo "  Or run:   bash ~/Desktop/Start-Jar.command"
echo ""
echo "  What was installed (all isolated, nothing system-wide):"
echo "  - pyenv + Python 3.11.9  → ~/.pyenv/"
echo "  - Jar app + venv         → ~/JAR/"
echo "  - Ollama + Qwen2.5 3B   → ~/.ollama/"
echo "  - Desktop launcher       → ~/Desktop/Start-Jar.command"
echo ""
echo "  To uninstall completely:"
echo "  rm -rf ~/JAR ~/.pyenv ~/.ollama ~/Desktop/Start-Jar.command"
echo "  brew uninstall --cask ollama"
echo ""
echo "  No internet required after installation."
echo "  No API keys needed. Everything runs locally."
echo ""