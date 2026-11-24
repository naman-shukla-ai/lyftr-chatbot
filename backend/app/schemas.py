"""Request/response models (the API contract)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str
    system_prompt: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("session_id")
    @classmethod
    def _session_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("session_id must not be blank")
        return v

    @field_validator("message")
    @classmethod
    def _message_len(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 4000:
            raise ValueError("message must be 1-4000 characters after trimming whitespace")
        return v

    @field_validator("system_prompt")
    @classmethod
    def _blank_prompt_is_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        return v.strip()


class Metadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str]
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: Metadata


class StoredMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    metadata: Optional[dict[str, Any]] = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[StoredMessage]
