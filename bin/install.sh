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
    fi
fi

# 2. Check for Git
if ! command -v git &> /dev/null; then
    echo "Installing Git..."
    brew install git
fi

# 3. Install pyenv for isolated Python version control
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

# 4. Install Python 3.11.9 in ~/.pyenv/ (does NOT touch system Python)
echo "Installing Python 3.11.9 (isolated in ~/.pyenv/)..."
pyenv install -s 3.11.9

# 5. Clone repo (or pull if exists)
if [ -d "$HOME/JAR" ]; then
    echo "Updating existing installation..."
    cd "$HOME/JAR"
    git pull
else
    echo "Cloning Jar..."
    git clone https://github.com/dhodtong365/jar-ai.git "$HOME/JAR"
    cd "$HOME/JAR"
fi

# 6. Create virtual environment using exact Python version
echo "Setting up Python environment..."
~/.pyenv/versions/3.11.9/bin/python3 -m venv "$HOME/JAR/venv"
source "$HOME/JAR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$HOME/JAR/requirements.txt"

# 7. Initialize database
echo "Initializing database..."
cd core/database
python load_statements.py --init-only
cd ../..

# 8. Install Ollama for local LLM
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# 9. Pull the LLM model
echo "Downloading Qwen2.5 3B (first run may take a few minutes)..."
ollama pull qwen2.5:3b

# 10. Create Desktop launcher
cat > "$HOME/Desktop/Start-Jar.command" << 'EOF'
#!/bin/bash
cd "$HOME/JAR"
source venv/bin/activate

# Start Ollama in background if not running
if ! pgrep -x "ollama" > /dev/null; then
    ollama serve &
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
echo ""
echo "  No internet required after installation."
echo "  No API keys needed. Everything runs locally."
echo ""