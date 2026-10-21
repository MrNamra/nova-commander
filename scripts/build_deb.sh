#!/usr/bin/env bash
# Build Debian package for WinSCP-Linux
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=========================================="
echo " Building WinSCP-Linux .deb Package"
echo "=========================================="

python3 "$SCRIPT_DIR/build_deb.py"

echo ""
echo "Installation command:"
echo "  sudo dpkg -i \"$REPO_ROOT/dist/nova-commander_1.0.0_amd64.deb\""
echo "  sudo apt-get install -f   # (if any missing dependencies)"
echo "=========================================="
