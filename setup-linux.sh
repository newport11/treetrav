#!/bin/bash

# Deactivate virtual environment if it's activated
if [[ $VIRTUAL_ENV != "" ]]; then
    echo "Deactivating existing virtual environment..."
    deactivate
fi

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python is not installed. Please install Python first."
    exit 1
fi

# Check if virtualenv directory exists and delete if it does
if [ -d "venv" ]; then
    echo "Deleting existing virtual environment..."
    rm -rf venv
fi

# Create a virtual environment (need to install python3.12 on your system first)
python3.12 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install dependencies from requirements.txt
if [ -f requirements.txt ]; then
    pip3 install -r requirements.txt
    echo "Dependencies installed from requirements.txt."
else
    echo "requirements.txt file not found. No dependencies installed."
fi

echo "Virtual environment is created. Run `source venv/bin/activate` to activate the environment"

deactivate