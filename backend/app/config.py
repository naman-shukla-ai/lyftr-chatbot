"""Application settings, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


@dataclass
class Settings:
    # Comma-separated key pool. Key ids are key_1, key_2, ... in this order.
    api_keys: list[str] = field(default_factory=list)
    # LiteLLM model name used for every key in the pool.
    model: str = "gemini/gemini-3.5-flash-lite"
    database_url: str = "sqlite:///./data/app.db"
    allowed_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    enable_failure_simulation: bool = False
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # LiteLLM's built-in in-memory cache.
    cache_ttl_seconds: int = 3600
    request_timeout_seconds: float = 30.0
    # Only the most recent N stored messages are sent to the model as context.
    max_history_messages: int = 40

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            api_keys=_split(env("LLM_API_KEYS")),
            model=env("LLM_MODEL", cls.model),
            database_url=env("DATABASE_URL", cls.database_url),
            allowed_origins=_split(env("ALLOWED_ORIGINS")) or ["http://localhost:3000"],
            enable_failure_simulation=_bool(env("ENABLE_FAILURE_SIMULATION")),
            default_system_prompt=env("DEFAULT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
            cache_ttl_seconds=int(env("CACHE_TTL_SECONDS", "3600")),
            request_timeout_seconds=float(env("REQUEST_TIMEOUT_SECONDS", "30")),
            max_history_messages=int(env("MAX_HISTORY_MESSAGES", "40")),
        )
