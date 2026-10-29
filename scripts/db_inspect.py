#!/usr/bin/env python3
"""Source-tree wrapper for the portable read-only SQLite inspector."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[1] / "portable-agent-kit/scripts/db_inspect.py"), run_name="__main__")
