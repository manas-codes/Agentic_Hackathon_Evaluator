#!/usr/bin/env python
"""Launcher. Adds src/ to the import path so no install step is needed.

    python cag.py --help
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cag101.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
