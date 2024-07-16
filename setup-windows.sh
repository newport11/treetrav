#!/bin/bash

# Deactivate virtual environment if it's activated
if [[ $VIRTUAL_ENV != "" ]]; then
    echo "Deactivating existing virtual environment..."
    deactivate
fi

# Check if Python is installed
if ! command -v python &> /dev/null; then
    echo "Python is not installed. Please install Python first."
    exit 1
fi

# Check if venv directory exists and delete if it does
if [ -d "venv" ]; then
    echo "Deleting existing virtual environment..."
    rm -rf venv
fi

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
source venv/Scripts/activate

# Install dependencies from requirements.txt
if [ -f requirements.txt ]; then
    pip install -r requirements.txt
    echo "Dependencies installed from requirements.txt."
else
    echo "requirements.txt file not found. No dependencies installed."
fi

echo "Virtual environment is created. Run `source venv/Scripts/activate` to activate the environment"

# Deactivate the virtual environment
deactivate