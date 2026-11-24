import litellm
import pytest

from app.llm import parse_simulation


def chat(client, message="hello failover", session="f", headers=None):
    return client.post("/chat", json={"session_id": session, "message": message}, headers=headers or {})


def err(cls):
    return cls(message="boom", llm_provider="gemini", model="m")


# ---------------------------------------------------------------- simulation header
@pytest.mark.parametrize("hdr", ["key_1=429", "key_1=500", "key_1=502", "key_1=503"])
def test_simulated_failure_of_key_1_fails_over_to_key_2(client, hdr):
    r = chat(client, headers={"X-Simulate-Failure": hdr}, message=f"m {hdr}")
    assert r.status_code == 200
    m = r.json()["metadata"]
    assert m["key_id"] == "key_2" and m["fallback_attempts"] == 1
    assert m["resolution_source"] == "LIVE_INFERENCE"
    assert r.json()["reply"].startswith("echo:")  # a real answer, not an error wrapped in 200


def test_all_keys_simulated_is_503_and_nothing_stored(client):
    r = chat(client, headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/f/messages").status_code == 404


def test_both_keys_listed_is_503(client):
    r = chat(client, headers={"X-Simulate-Failure": "key_1=429,key_2=500"})
    assert r.status_code == 503


def test_simulation_applies_to_one_request_only(client):
    chat(client, headers={"X-Simulate-Failure": "*=429"}, message="one")
    r = chat(client, message="one")
    assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_1"


def test_simulation_bypasses_cache_so_failover_is_always_demonstrable(client):
    chat(client, session="a", message="cached msg")  # populates the cache
    r = chat(client, session="b", message="cached msg", headers={"X-Simulate-Failure": "key_1=429"})
    assert r.json()["metadata"]["key_id"] == "key_2"
    r = chat(client, session="c", message="cached msg", headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503


def test_simulation_ignored_when_switch_is_off(client_factory):
    c = client_factory(enable_failure_simulation=False)
    r = chat(c, headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_1"


def test_simulation_does_not_consume_real_provider_calls(client, provider):
    chat(client, headers={"X-Simulate-Failure": "key_1=429"})
    assert provider.calls == ["secret-B"]  # key_1 was never actually used


def test_garbage_header_is_ignored(client):
    r = chat(client, headers={"X-Simulate-Failure": "nonsense,key_1=999,=429"})
    assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_1"


def test_parse_simulation():
    assert parse_simulation("key_1=429, key_2=500") == {"key_1": 429, "key_2": 500}
    assert parse_simulation("*=503") == {"*": 503}
    assert parse_simulation(None) == {}
    assert parse_simulation("key_1=404") == {}


# ---------------------------------------------------------------- real provider errors
@pytest.mark.parametrize(
    "exc",
    [litellm.RateLimitError, litellm.InternalServerError, litellm.ServiceUnavailableError,
     litellm.BadGatewayError, litellm.APIConnectionError, litellm.AuthenticationError],
)
def test_real_provider_errors_trigger_failover(client, provider, exc):
    provider.fail["secret-A"] = err(exc)
    r = chat(client, message=f"real {exc.__name__}")
    assert r.status_code == 200
    m = r.json()["metadata"]
    assert m["key_id"] == "key_2" and m["fallback_attempts"] == 1


def test_all_real_keys_failing_is_clean_503(client, provider):
    provider.fail["secret-A"] = err(litellm.RateLimitError)
    provider.fail["secret-B"] = RuntimeError("something unexpected")
    r = chat(client)
    assert r.status_code == 503 and r.json() == {"detail": "all providers unavailable"}
    assert "Traceback" not in r.text


def test_failed_request_not_in_history_but_retry_works(client, provider):
    provider.fail["secret-A"] = err(litellm.RateLimitError)
    provider.fail["secret-B"] = err(litellm.RateLimitError)
    assert chat(client, session="r").status_code == 503
    provider.fail.clear()
    assert chat(client, session="r").status_code == 200
    msgs = client.get("/sessions/r/messages").json()["messages"]
    assert len(msgs) == 2  # only the successful exchange


def test_empty_completion_fails_over(client, provider, monkeypatch):
    from app import llm as llm_mod

    real = llm_mod._litellm_call

    async def empty_for_a(**kw):
        if kw["api_key"] == "secret-A":
            return await litellm.acompletion(**{**kw, "mock_response": " "})
        return await real(**kw)

    monkeypatch.setattr(llm_mod, "_litellm_call", empty_for_a)
    r = chat(client, message="empty test")
    assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_2"


def test_server_never_raises_for_db_trouble(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(client.app.state.db, "save_exchange", boom)
    from fastapi.testclient import TestClient

    quiet = TestClient(client.app, raise_server_exceptions=False)
    r = quiet.post("/chat", json={"session_id": "x", "message": "hi"})
    assert r.status_code == 500 and r.json() == {"detail": "internal server error"}
