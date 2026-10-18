#!/usr/bin/env python3
"""Convenience runner for WinSCP-Linux from repository root."""
import os
import sys
from pathlib import Path

# Ensure Qt 6.5+ xcb-cursor0 dependency is loaded seamlessly
if os.environ.get("_WINSCP_XCB_LOADED") != "1":
    candidates = [
        Path(__file__).resolve().parent / "winscp_linux" / "lib",
        Path.home() / ".local" / "lib",
    ]
    for c in candidates:
        if (c / "libxcb-cursor.so.0").exists():
            lib_path = str(c)
            current_ld = os.environ.get("LD_LIBRARY_PATH", "")
            if lib_path not in current_ld.split(":"):
                os.environ["_WINSCP_XCB_LOADED"] = "1"
                os.environ["LD_LIBRARY_PATH"] = f"{lib_path}:{current_ld}" if current_ld else lib_path
                try:
                    argv = getattr(sys, "orig_argv", [sys.executable] + sys.argv)
                    os.execv(sys.executable, argv)
                except Exception:
                    pass
            break

# Add winscp_linux and winscp_linux/src to sys.path
root_dir = Path(__file__).resolve().parent
winscp_dir = root_dir / "winscp_linux"
src_dir = winscp_dir / "src"

sys.path.insert(0, str(winscp_dir))
sys.path.insert(0, str(src_dir))

from src.main import main

if __name__ == "__main__":
    main()
