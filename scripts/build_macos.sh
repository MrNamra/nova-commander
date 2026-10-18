#!/bin/bash
# =============================================================================
# macOS Build Script for WinSCP Cross-Platform
# =============================================================================
set -e

echo "==================================================="
echo "Building WinSCP for macOS (.app & .dmg)"
echo "==================================================="

# Verify Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not found!"
    exit 1
fi

echo "[1/3] Installing/verifying dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller

echo "[2/3] Building native macOS bundle..."
python3 scripts/build_macos.py

echo "==================================================="
echo "✔ macOS Build Complete! Check the 'dist/' folder."
echo "==================================================="
