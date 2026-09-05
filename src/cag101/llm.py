"""Anthropic API wrapper: structured output, caching, retries, cost accounting.

Design notes that matter for defensibility:

* **No `temperature`.** It is rejected with HTTP 400 on Opus 5 and Sonnet 5.
  Repeatability comes from the fixed rubric, anchored bands, JSON-schema-
  constrained output and multiple independent passes — not a sampling knob.
* **Structured output** via `output_config.format` with a schema generated from
  the rubric YAML, so a scorer physically cannot return a criterion the rubric
  does not define or omit one it does.
* **Prompt caching** on the instruction + rubric prefix. That prefix is byte-
  identical for every submission scored on a given criterion, which is where the
  saving on a 400-submission run comes from.
* **Every call's token usage and cost is recorded**, and a run-level ceiling
  halts the run rather than overspending.
"""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .config import CREDENTIAL_HELP, anthropic_api_key, load_config

# ---------------------------------------------------------------------------
# Pricing — published Anthropic first-party rates, USD per million tokens.
# Verify against https://www.anthropic.com/pricing before quoting figures.
# ---------------------------------------------------------------------------
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5-1": (10.00, 50.00),
}

# Cache reads cost about a tenth of the input rate; cache writes about 1.25x.
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


class CostCeilingExceeded(RuntimeError):
    """Raised when a run would exceed cost.max_run_cost_inr."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0
    cost_inr: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.calls += other.calls
        self.cost_inr += other.cost_inr

    def summary(self) -> str:
        return (
            f"{self.calls} call(s), "
            f"in {self.input_tokens:,} (+{self.cache_read_tokens:,} cached), "
            f"out {self.output_tokens:,}, "
            f"Rs. {self.cost_inr:,.2f}"
        )


def price_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Cost in INR. Unknown models fall back to Opus rates so an estimate is
    never silently zero."""
    cfg = load_config()
    rate_in, rate_out = PRICING_USD_PER_MTOK.get(model, PRICING_USD_PER_MTOK["claude-opus-5"])
    usd = (
        input_tokens * rate_in
        + cache_read_tokens * rate_in * CACHE_READ_MULTIPLIER
        + cache_write_tokens * rate_in * CACHE_WRITE_MULTIPLIER
        + output_tokens * rate_out
    ) / 1_000_000
    return usd * float(cfg.get("cost.usd_to_inr", 88.0))


@dataclass
class CostMeter:
    """Thread-safe run-level spend tracker with a hard ceiling."""

    ceiling_inr: float
    warn_at: float = 0.7
    usage: Usage = field(default_factory=Usage)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _warned: bool = False

    def record(self, usage: Usage) -> None:
        with self._lock:
            self.usage.add(usage)
            spent = self.usage.cost_inr
            if not self._warned and spent >= self.ceiling_inr * self.warn_at:
                self._warned = True
                print(
                    f"  [cost] Rs. {spent:,.2f} of the Rs. {self.ceiling_inr:,.2f} "
                    f"ceiling used ({spent / self.ceiling_inr:.0%})."
                )

    def check(self) -> None:
        with self._lock:
            if self.usage.cost_inr >= self.ceiling_inr:
                raise CostCeilingExceeded(
                    f"Run halted: Rs. {self.usage.cost_inr:,.2f} spent, ceiling is "
                    f"Rs. {self.ceiling_inr:,.2f}. Raise cost.max_run_cost_inr in "
                    "config.yaml to continue, or reduce the batch."
                )

    @classmethod
    def from_config(cls) -> "CostMeter":
        cfg = load_config()
        return cls(
            ceiling_inr=float(cfg.get("cost.max_run_cost_inr", 15000)),
            warn_at=float(cfg.get("cost.warn_at_fraction", 0.7)),
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
_client: anthropic.Anthropic | None = None
_client_lock = threading.Lock()


def get_client() -> anthropic.Anthropic:
    """Build the client, letting the SDK resolve credentials when no explicit
    key is set — so a Console API key, an `ant auth login` OAuth profile, or
    federated credentials on a server all work without a code change."""
    global _client
    with _client_lock:
        if _client is None:
            cfg = load_config()
            kwargs: dict[str, Any] = {
                "max_retries": int(cfg.get("llm.max_retries", 5)),
                "timeout": 600.0,
            }
            key = anthropic_api_key()
            if key:
                kwargs["api_key"] = key
            try:
                _client = anthropic.Anthropic(**kwargs)
            except Exception as exc:
                raise RuntimeError(f"{CREDENTIAL_HELP}\n\n(SDK reported: {exc})") from exc
        return _client


def describe_credential_source() -> str:
    """Which credential the run will use. Printed at the start of a run so the
    audit trail records it — on a shared machine it should never be a guess."""
    import os

    if os.environ.get("ANTHROPIC_API_KEY"):
        key = os.environ["ANTHROPIC_API_KEY"]
        return f"ANTHROPIC_API_KEY (…{key[-4:]})"
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "ANTHROPIC_AUTH_TOKEN (OAuth bearer token)"
    if os.environ.get("ANTHROPIC_PROFILE"):
        return f"OAuth profile {os.environ['ANTHROPIC_PROFILE']!r}"
    config_dir = os.environ.get("ANTHROPIC_CONFIG_DIR") or os.path.join(
        os.environ.get("APPDATA", ""), "Anthropic"
    )
    if config_dir and os.path.isdir(os.path.join(config_dir, "credentials")):
        return "default OAuth profile on disk"
    return "none found — the run will fail at the first model call"


@dataclass
class LLMResult:
    data: dict[str, Any]
    usage: Usage
    model: str
    raw_text: str
    stop_reason: str | None = None


def _build_system(
    instructions: str, cacheable_prefix: str | None
) -> list[dict[str, Any]] | str:
    """System prompt as blocks so the stable prefix can be cached.

    Order matters: the cacheable prefix (instructions + rubric) comes first and
    is byte-identical across submissions; anything volatile belongs in the user
    message, after the breakpoint.
    """
    cfg = load_config()
    if not cacheable_prefix:
        return instructions
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": instructions},
        {"type": "text", "text": cacheable_prefix},
    ]
    if bool(cfg.get("llm.enable_prompt_caching", True)):
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def call_json(
    *,
    instructions: str,
    user_content: str,
    schema: dict[str, Any],
    model: str | None = None,
    cacheable_prefix: str | None = None,
    effort: str | None = None,
    max_tokens: int | None = None,
    meter: CostMeter | None = None,
) -> LLMResult:
    """One structured-output call. Returns parsed JSON matching `schema`.

    The schema constraint is what makes the rest of the pipeline safe: a scorer
    cannot invent a criterion, skip one, or return prose where a number belongs.
    """
    cfg = load_config()
    model = model or str(cfg.require("llm.scoring_model"))
    effort = effort or str(cfg.get("llm.effort", "high"))
    max_tokens = max_tokens or int(cfg.get("llm.max_output_tokens", 16000))

    if meter is not None:
        meter.check()

    client = get_client()
    attempts = int(cfg.get("llm.max_retries", 5))
    base_delay = float(cfg.get("llm.retry_base_delay_seconds", 2))
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=_build_system(instructions, cacheable_prefix),
                messages=[{"role": "user", "content": user_content}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
            break
        except anthropic.RateLimitError as exc:
            last_error = exc
            retry_after = float(
                getattr(exc, "response", None).headers.get("retry-after", 0)
                if getattr(exc, "response", None) is not None
                else 0
            ) or base_delay * (2**attempt)
            time.sleep(min(retry_after + random.uniform(0, 1), 120))
        except anthropic.APIStatusError as exc:
            last_error = exc
            if exc.status_code < 500:
                # 400/404 are our bug (bad schema, bad model id) — do not retry.
                raise
            time.sleep(min(base_delay * (2**attempt) + random.uniform(0, 1), 120))
        except anthropic.APIConnectionError as exc:
            last_error = exc
            time.sleep(min(base_delay * (2**attempt) + random.uniform(0, 1), 120))
    else:
        raise RuntimeError(f"Model call failed after {attempts} attempts: {last_error}")

    raw_usage = response.usage
    usage = Usage(
        input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
        output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
        calls=1,
    )
    usage.cost_inr = price_usage(
        model,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
    )
    if meter is not None:
        meter.record(usage)

    # A refusal is not a parse failure — surface it as one so the caller can
    # flag the submission for human review rather than record a score of zero.
    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        raise RuntimeError(
            "The model declined to process this submission "
            f"(category: {getattr(detail, 'category', 'unknown')}). "
            "Flag for human review rather than scoring it."
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        raise RuntimeError(
            f"Empty response (stop_reason={response.stop_reason}). "
            "If stop_reason is max_tokens, raise llm.max_output_tokens."
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Structured output did not parse as JSON: {exc}\nFirst 500 chars: {text[:500]}"
        ) from exc

    return LLMResult(
        data=data,
        usage=usage,
        model=model,
        raw_text=text,
        stop_reason=response.stop_reason,
    )


# ---------------------------------------------------------------------------
# Token estimation for dry runs
# ---------------------------------------------------------------------------
def count_tokens(
    *,
    instructions: str,
    user_content: str,
    cacheable_prefix: str | None = None,
    model: str | None = None,
) -> int:
    """Exact input token count via the API. Used by `score --dry-run` so a cost
    estimate is measured rather than guessed at four characters per token."""
    cfg = load_config()
    model = model or str(cfg.require("llm.scoring_model"))
    client = get_client()
    system = _build_system(instructions, cacheable_prefix)
    # count_tokens does not accept cache_control blocks in every SDK version;
    # strip them for the estimate.
    if isinstance(system, list):
        system = [{k: v for k, v in b.items() if k != "cache_control"} for b in system]
    result = client.messages.count_tokens(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    return int(result.input_tokens)
