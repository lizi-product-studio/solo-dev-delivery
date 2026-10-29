#!/usr/bin/env python3
"""Source-tree wrapper for the independent evidence verifier."""
from pathlib import Path
import runpy


runpy.run_path(str(Path(__file__).resolve().parents[1] /
                   "portable-agent-kit/scripts/independent_verify.py"), run_name="__main__")
