"""Vercel entry point.

Vercel's Python runtime looks for a module-level ASGI application called `app`.
The portal itself is unchanged - this file only puts `src/` on the import path,
because the project keeps its package under `src/` and the deployment bundle
has no equivalent of `--app-dir`.

Only the portal runs here. Ingestion, mapping and scoring stay on an operator
machine: they need the submission files, they need an API credential, and they
run for hours - none of which belongs in a request handler.
"""

from __future__ import annotations

import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE_ROOT / "src"))

from cag101.web.app import app  # noqa: E402

__all__ = ["app"]
