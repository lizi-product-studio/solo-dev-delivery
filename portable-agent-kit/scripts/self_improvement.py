#!/usr/bin/env python3
"""CLI for the evidence-bound self-improvement coordinator."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from control_plane.learning import main


if __name__ == "__main__":
    raise SystemExit(main())
