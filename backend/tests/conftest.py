"""Test harness.

The provider call is stubbed at `app.llm._litellm_call`, but the stub delegates to the
real `litellm.acompletion(mock_response=...)`, so LiteLLM's own cache, usage accounting
and exception types are all exercised.
"""
import litellm
import pytest
from fastapi.testclient import TestClient

from app import llm as llm_mod
from app.config import Settings
from app.main import create_app


class FakeProvider:
    """Controls how each API key behaves. `fail` maps api_key -> exception to raise."""

    def __init__(self):
        self.fail: dict[str, Exception] = {}
        self.calls: list[str] = []  # api keys that reached the "provider" (cache hits excluded)
        self.last_messages: list[dict] = []

    async def __call__(self, **kwargs):
        key = kwargs["api_key"]
        self.last_messages = kwargs["messages"]
        if key in self.fail:
            self.calls.append(key)
            raise self.fail[key]
        last = kwargs["messages"][-1]["content"]
        resp = await litellm.acompletion(**{**kwargs, "mock_response": f"echo: {last}"})
        if not resp._hidden_params.get("cache_hit"):
            self.calls.append(key)  # only count calls that really reached the "provider"
        return resp


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(llm_mod, "_litellm_call", fake)
    return fake


def make_settings(tmp_path, **over) -> Settings:
    base = dict(
        api_keys=["secret-A", "secret-B"],
        model="gemini/gemini-test",
        database_url=f"sqlite:///{tmp_path}/test.db",
        allowed_origins=["http://localhost:3000"],
        enable_failure_simulation=True,
        cache_ttl_seconds=600,
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture()
def client_factory(tmp_path, provider):
    clients = []

    def _make(**over):
        c = TestClient(create_app(make_settings(tmp_path, **over)))
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture()
def client(client_factory):
    return client_factory()
