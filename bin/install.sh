#!/bin/bash
# Jar Finance — Mac Setup
# Run: bash install.sh

set -e

echo "🫙 Installing Jar..."

# 1. Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# 2. Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Installing Python..."
    brew install python@3.11
fi

# 3. Check for Git
if ! command -v git &> /dev/null; then
    echo "Installing Git..."
    brew install git
fi

# 4. Clone repo (or pull if exists)
if [ -d "$HOME/JAR" ]; then
    echo "Updating existing installation..."
    cd "$HOME/JAR"
    git pull
else
    echo "Cloning Jar..."
    git clone https://github.com/dhodtong365/jar-ai.git "$HOME/JAR"
    cd "$HOME/JAR"
fi

# 5. Create virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install docling anthropic fastapi uvicorn python-multipart openai

# 6. Initialize database
cd core/database
python load_statements.py --init-only
cd ../..

# 7. Install Ollama
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# 8. Pull the LLM model
echo "Downloading Qwen2.5 3B (first run may take a few minutes)..."
ollama pull qwen2.5:3b

# 9. Create Desktop launcher
cat > "$HOME/Desktop/Start-Jar.command" << 'EOF'
#!/bin/bash
cd "$HOME/JAR"
source venv/bin/activate

# Start Ollama in background if not running
if ! pgrep -x "ollama" > /dev/null; then
    ollama serve &
    sleep 2
fi

echo "🫙 Starting Jar at http://localhost:5000"
open http://localhost:5000
python app/server.py
EOF
chmod +x "$HOME/Desktop/Start-Jar.command"

echo ""
echo "✅ Jar installed successfully!"
echo ""
echo "To start: double-click Start-Jar on your Desktop"
echo "Or run: bash ~/Desktop/Start-Jar.command"
echo ""
echo "No internet required after installation."
echo "No API keys needed. Everything runs locally."
echo ""
