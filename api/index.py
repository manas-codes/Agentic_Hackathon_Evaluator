"""Vercel entry point.

Vercel's Python runtime looks for a module-level ASGI application called `app`.
The portal itself is unchanged - this file puts `src/` on the import path
(the project keeps its package under `src/` and a deployment bundle has no
equivalent of uvicorn's `--app-dir`) and corrects the request path.

Why the path needs correcting
-----------------------------
Every URL is routed to this function by a rewrite in vercel.json. Vercel now
passes the *rewritten* destination through to the function rather than the URL
the browser asked for, so a request for `/scorecards` arrives with a path of
`/api/index/scorecards`. FastAPI has no route by that name and answers 404 for
every page including the login screen.

`_StripPrefix` removes the mount prefix before the request reaches the app. It
is written to be a no-op when the prefix is absent, so it stays correct if
Vercel reverts to passing the original path, and it leaves local `uvicorn` runs
completely unaffected.

Only the portal runs here. Ingestion, mapping and scoring stay on an operator
machine: they need the submission files, they need an API credential, and they
run for hours - none of which belongs in a request handler.
"""

from __future__ import annotations

import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE_ROOT / "src"))

from cag101.web.app import app as _portal  # noqa: E402

MOUNT_PREFIX = "/api/index"


class _StripPrefix:
    """Remove the function's mount prefix from the request path.

    Both `path` and `raw_path` are corrected: Starlette routes on `path`, but
    `raw_path` is what several middlewares and any URL the app builds for a
    redirect are derived from, and leaving the two disagreeing produces
    redirects that loop.
    """

    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = prefix.rstrip("/")

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self.prefix or path.startswith(self.prefix + "/"):
                trimmed = path[len(self.prefix):] or "/"
                scope = dict(scope)
                scope["path"] = trimmed
                raw = scope.get("raw_path")
                if isinstance(raw, bytes):
                    encoded = self.prefix.encode()
                    if raw.startswith(encoded):
                        scope["raw_path"] = raw[len(encoded):] or b"/"
        await self.app(scope, receive, send)


app = _StripPrefix(_portal, MOUNT_PREFIX)

__all__ = ["app"]
