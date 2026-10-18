#!/usr/bin/env python3
"""Convenience runner for WinSCP-Linux from repository root."""
import sys
from pathlib import Path

# Add winscp_linux and winscp_linux/src to sys.path
root_dir = Path(__file__).resolve().parent
winscp_dir = root_dir / "winscp_linux"
src_dir = winscp_dir / "src"

sys.path.insert(0, str(winscp_dir))
sys.path.insert(0, str(src_dir))

from src.main import main

if __name__ == "__main__":
    main()
