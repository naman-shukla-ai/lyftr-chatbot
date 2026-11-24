"""LiteLLM gateway: key pool with ordered failover, built-in response cache,
and the `X-Simulate-Failure` test hook.

Why a manual failover loop instead of Router fallbacks?  The API contract needs
deterministic behaviour: keys are tried in order (key_1, key_2, ...), we must
report *which* key answered and how many failed before it, and the failure
simulation header has to hit exact keys. A small explicit loop around
`litellm.acompletion` gives that precisely, while LiteLLM still does the
provider call, usage accounting and caching.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import litellm
from litellm import Cache

from .config import Settings

litellm.suppress_debug_info = True
litellm.telemetry = False
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)
log = logging.getLogger("chatbot.llm")

SIMULATABLE_STATUSES = {429, 500, 502, 503}
LIVE = "LIVE_INFERENCE"
CACHED = "LLM_CACHE"


class AllProvidersUnavailable(Exception):
    """Every key in the pool failed (or none is configured)."""


@dataclass
class LLMResult:
    reply: str
    resolution_source: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str]
    fallback_attempts: int

    def metadata(self) -> dict[str, Any]:
        return {
            "resolution_source": self.resolution_source,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "key_id": self.key_id,
            "fallback_attempts": self.fallback_attempts,
        }


def parse_simulation(header: Optional[str]) -> dict[str, int]:
    """Parse `key_1=429,key_2=500` or `*=429` into {key_id_or_*: status}.

    Malformed pairs and unsupported statuses are ignored.
    """
    out: dict[str, int] = {}
    for part in (header or "").split(","):
        if "=" not in part:
            continue
        key, _, status = part.partition("=")
        key, status = key.strip(), status.strip()
        if key and status.isdigit() and int(status) in SIMULATABLE_STATUSES:
            out[key] = int(status)
    return out


def _simulated_error(status: int, model: str) -> Exception:
    """An exception shaped like what the provider/LiteLLM would raise for `status`."""
    cls = {
        429: litellm.RateLimitError,
        500: litellm.InternalServerError,
        502: litellm.BadGatewayError,
        503: litellm.ServiceUnavailableError,
    }[status]
    return cls(message=f"simulated provider error {status}", llm_provider="simulated", model=model)


async def _litellm_call(**kwargs: Any) -> Any:
    # Indirection so tests can stub the provider while still exercising LiteLLM's cache.
    return await litellm.acompletion(**kwargs)


class LLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.model
        self.timeout = settings.request_timeout_seconds
        # Ordered key pool; ids are key_1, key_2, ... Only ids are ever logged/returned.
        self.keys: dict[str, str] = {f"key_{i}": k for i, k in enumerate(settings.api_keys, start=1)}
        # LiteLLM's built-in in-memory cache, keyed by model + the full message list.
        litellm.cache = Cache(type="local", ttl=settings.cache_ttl_seconds)
        self.cache_enabled = True

    async def warmup(self) -> None:
        """Pay LiteLLM's one-time lazy-init cost at startup so the first cache hit is fast."""
        try:
            msgs = [{"role": "user", "content": "warmup"}]
            for _ in range(2):
                r = await litellm.acompletion(model=self.model, messages=msgs, api_key="warmup", mock_response="ok")
                if not r._hidden_params.get("cache_hit"):
                    await litellm.cache.async_add_cache(r, model=self.model, messages=msgs)
        except Exception:  # warm-up is best effort
            log.debug("warmup skipped", exc_info=True)

    async def complete(self, messages: list[dict[str, str]], simulate: Optional[dict[str, int]] = None) -> LLMResult:
        simulate = simulate or {}
        started = time.perf_counter()
        # We write to the cache ourselves right after a live answer (deterministic,
        # unlike LiteLLM's background write), so disable the automatic store.
        # A simulated-failure request skips the cache *read* so the failover path
        # is always exercised and key_id is reported.
        cache_ctl = {"no-store": True}
        if simulate:
            cache_ctl["no-cache"] = True

        failed = 0
        for key_id, api_key in self.keys.items():
            status = simulate.get(key_id, simulate.get("*"))
            try:
                if status:
                    raise _simulated_error(status, self.model)
                resp = await _litellm_call(
                    model=self.model,
                    messages=messages,
                    api_key=api_key,
                    timeout=self.timeout,
                    num_retries=0,  # failover across keys is handled here
                    cache=cache_ctl,
                )
                reply = (resp.choices[0].message.content or "").strip()
                if not reply:
                    raise ValueError("empty completion")
            except Exception as exc:  # any provider failure -> try the next key
                failed += 1
                log.warning("key %s failed (%s: %s); failing over", key_id, type(exc).__name__, _brief(exc))
                continue

            cache_hit = bool(getattr(resp, "_hidden_params", {}).get("cache_hit"))
            if not cache_hit:
                try:
                    await litellm.cache.async_add_cache(resp, model=self.model, messages=messages)
                except Exception:
                    log.warning("could not write response to cache", exc_info=True)

            usage = getattr(resp, "usage", None)
            return LLMResult(
                reply=reply,
                resolution_source=CACHED if cache_hit else LIVE,
                model=self.model,
                latency_ms=max(1, round((time.perf_counter() - started) * 1000)),
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                key_id=None if cache_hit else key_id,
                fallback_attempts=0 if cache_hit else failed,
            )

        raise AllProvidersUnavailable()


def _brief(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    return text[:120]
