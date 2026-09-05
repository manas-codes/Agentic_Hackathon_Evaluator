"""Configuration loading. Everything scoring-relevant comes from config.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


class Config:
    """Nested config with dotted lookup: cfg.get("llm.scoring_model")."""

    def __init__(self, data: dict[str, Any], root: Path):
        self._data = data
        self.root = root

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted, _MISSING)
        if value is _MISSING:
            raise KeyError(f"Required config key missing: {dotted}")
        return value

    def path(self, dotted: str) -> Path:
        """Resolve a configured path relative to the project root."""
        raw = self.require(dotted)
        p = Path(raw)
        return p if p.is_absolute() else (self.root / p)

    @property
    def raw(self) -> dict[str, Any]:
        return self._data


_MISSING = object()


@lru_cache(maxsize=1)
def load_config() -> Config:
    config_path = Path(os.environ.get("CAG101_CONFIG", PROJECT_ROOT / "config.yaml"))
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Config(data, PROJECT_ROOT)


def anthropic_api_key() -> str | None:
    """An explicitly set API key, or None to let the SDK resolve credentials.

    Returning None matters: the SDK resolves credentials in a fixed order —
    ANTHROPIC_API_KEY, then ANTHROPIC_AUTH_TOKEN, then an `ant auth login`
    OAuth profile, then Workload Identity Federation. Passing an explicit key
    of None lets that chain run, so the framework works with a Console API key,
    an OAuth profile, or federated credentials on a server, without a code
    change for each.
    """
    return os.environ.get("ANTHROPIC_API_KEY", "").strip() or None


def clear_empty_credential_vars() -> None:
    """Remove blank credential variables from the environment.

    An empty ANTHROPIC_API_KEY is worse than an unset one: it still occupies
    its slot in the SDK's resolution order and authenticates with an empty key,
    silently shadowing any OAuth profile. A `.env` file with a placeholder line
    like `ANTHROPIC_API_KEY=` produces exactly that, so strip it here rather
    than leaving a confusing 401 for someone to debug.

    Also drops a blank ANTHROPIC_AUTH_TOKEN, and warns if both a key and a
    token are set — the SDK sends both and the API rejects the request.
    """
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if name in os.environ and not os.environ[name].strip():
            del os.environ[name]

    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        import warnings

        warnings.warn(
            "Both ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN are set. The SDK "
            "sends both and the API rejects the request. Unset one.",
            stacklevel=2,
        )


clear_empty_credential_vars()


CREDENTIAL_HELP = """\
No Anthropic credential could be resolved. Any one of these works:

  1. A Console API key (recommended for the bulk run and for the portal —
     nothing depends on one person's browser session):
       put ANTHROPIC_API_KEY=sk-ant-... in F:\\CAG101-Evaluation\\.env

  2. An OAuth profile from your own account, for development on this machine:
       install the `ant` CLI, then run `ant auth login`
       (leave ANTHROPIC_API_KEY unset — a set key silently overrides profiles)

  3. Workload Identity Federation, for a server or CI deployment.

`ant auth status` shows which source is active and against which org and
workspace, if the CLI is installed."""


def session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if not secret or secret == "change-me":
        raise RuntimeError(
            "SESSION_SECRET is not set (or still the placeholder). Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return secret


def ensure_directories() -> None:
    cfg = load_config()
    for key in ("paths.submissions", "paths.cache", "paths.exports"):
        cfg.path(key).mkdir(parents=True, exist_ok=True)
    cfg.path("paths.database").parent.mkdir(parents=True, exist_ok=True)
