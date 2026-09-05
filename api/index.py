"""Vercel entry point.

Vercel's Python runtime imports `app` from this module and serves it as an ASGI
application. Everything else about the portal is unchanged - this file only
puts `src/` on the import path, because the local runner does that with
`uvicorn --app-dir src` and there is no equivalent flag here.

What this deployment is, and is not:

  * It serves the portal - scores, rationale, evidence quotes, sign-off.
  * It holds no Anthropic credential and makes no model calls. Scoring runs on
    a machine that has the submissions; this only reads what that run recorded.
  * It has no submission files, so the source documents cannot be served from
    here. That is a known gap against the local deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cag101.web.app import app  # noqa: E402

__all__ = ["app"]
