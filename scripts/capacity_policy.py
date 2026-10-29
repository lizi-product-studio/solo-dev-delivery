#!/usr/bin/env python3
"""Source-tree wrapper for the portable capacity recovery policy."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[1] / "portable-agent-kit/scripts/capacity_policy.py"), run_name="__main__")
