#!/bin/bash
# Jar — activate venv and start interactive shell

cd "$(dirname "$0")/.."

# Check if the virtual environment folder exists
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Run bin/install.sh first."
    exit 1
fi

# Activate the virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Confirm activation
echo "Using Python: $(python --version) at $(which python)"

# Start a new interactive shell
exec bash
