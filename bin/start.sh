#!/bin/bash

PYTHON="/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11"

# Check if the virtual environment folder exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment with Python 3.11..."
    "$PYTHON" -m venv venv
else
    echo "Virtual environment already exists."
fi

# Activate the virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Confirm activation by showing the Python interpreter path
echo "Using Python: $(python --version) at $(which python)"

# Start a new interactive shell
exec bash

