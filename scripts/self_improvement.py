#!/usr/bin/env python3
"""Source-tree wrapper for the bounded self-improvement coordinator."""
from pathlib import Path
import runpy


runpy.run_path(str(Path(__file__).resolve().parents[1] /
                   "portable-agent-kit/scripts/self_improvement.py"), run_name="__main__")
