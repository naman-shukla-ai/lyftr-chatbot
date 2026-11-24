import json
import sqlite3

import pytest


def chat(client, session_id="s1", message="What is the capital of France?", headers=None, **extra):
    return client.post("/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_health_without_keys_is_503(client_factory):
    c = client_factory(api_keys=[])
    r = c.get("/health")
    assert r.status_code == 503
    assert r.json()["keys_configured"] == 0


def test_chat_returns_contract_fields(client):
    r = chat(client)
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "s1"
    assert body["reply"] == "echo: What is the capital of France?"
    m = body["metadata"]
    assert set(m) >= {
        "resolution_source", "model", "latency_ms", "input_tokens",
        "output_tokens", "key_id", "fallback_attempts",
    }
    assert m["resolution_source"] == "LIVE_INFERENCE"
    assert m["model"] == "gemini/gemini-test"
    assert m["key_id"] == "key_1" and m["fallback_attempts"] == 0
    assert isinstance(m["latency_ms"], int) and m["latency_ms"] >= 1
    assert m["input_tokens"] > 0 and m["output_tokens"] > 0


def test_api_key_never_leaks(client):
    text = chat(client).text + client.get("/sessions/s1/messages").text
    assert "secret-A" not in text and "secret-B" not in text


# ---------------------------------------------------------------- caching
def test_same_first_message_in_new_session_is_cache_hit(client, provider):
    first = chat(client, "sess-a").json()
    second = chat(client, "sess-b").json()
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    m = second["metadata"]
    assert m["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert m["key_id"] is None and m["fallback_attempts"] == 0
    assert m["input_tokens"] == first["metadata"]["input_tokens"]
    assert m["output_tokens"] == first["metadata"]["output_tokens"]
    assert len(provider.calls) == 1  # the second request never reached the provider


def test_different_message_never_hits_cache(client):
    chat(client, "a", "hello")
    r = chat(client, "b", "hello there")
    assert r.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_different_system_prompt_misses_cache(client):
    chat(client, "a", "hello")
    r = chat(client, "b", "hello", system_prompt="You are a pirate.")
    assert r.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_cache_hit_is_faster_than_live(client):
    live = chat(client, "a", "speed test").json()["metadata"]["latency_ms"]
    hit = chat(client, "b", "speed test").json()["metadata"]
    assert hit["resolution_source"] == "LLM_CACHE"
    assert hit["latency_ms"] <= live


def test_same_message_in_same_session_is_not_cached_because_history_differs(client):
    chat(client, "a", "ping")
    r = chat(client, "a", "ping")
    assert r.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# ---------------------------------------------------------------- multi-turn
def test_history_is_sent_to_the_model(client, provider):
    chat(client, "s", "my name is Naman")
    chat(client, "s", "what is my name?")
    sent = provider.last_messages
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "my name is Naman"
    assert sent[-1]["content"] == "what is my name?"


def test_sessions_are_isolated(client, provider):
    chat(client, "one", "secret in session one")
    chat(client, "two", "hello from two")
    assert all("secret in session one" not in m["content"] for m in provider.last_messages)


def test_history_endpoint(client):
    chat(client, "h", "hi")
    chat(client, "h", "again")
    r = client.get("/sessions/h/messages")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "hi" and "metadata" not in msgs[0]
    assert msgs[1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert msgs[1]["metadata"]["input_tokens"] > 0
    assert "system" not in [m["role"] for m in msgs]  # system prompt is not returned


def test_history_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}


def test_history_survives_restart(client_factory, tmp_path):
    c1 = client_factory()
    chat(c1, "persist", "remember me")
    c2 = client_factory()  # new app instance, same SQLite file
    msgs = c2.get("/sessions/persist/messages").json()["messages"]
    assert msgs[0]["content"] == "remember me"


def test_rows_are_stored_in_sqlite(client, tmp_path):
    chat(client, "db", "hello")
    rows = sqlite3.connect(f"{tmp_path}/test.db").execute(
        "SELECT role, content, metadata, created_at FROM messages WHERE session_id='db' ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == ["user", "assistant"]
    assert rows[0][2] is None and json.loads(rows[1][2])["resolution_source"] == "LIVE_INFERENCE"
    assert rows[0][3].endswith("+00:00")  # ISO-8601 UTC


def test_system_prompt_is_used_and_persists_for_the_session(client, provider):
    chat(client, "sp", "hi", system_prompt="You are a pirate.")
    chat(client, "sp", "again")  # no system_prompt: session keeps the earlier one
    assert provider.last_messages[0] == {"role": "system", "content": "You are a pirate."}


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "s", "message": ""},
        {"session_id": "s", "message": "    "},
        {"session_id": "s", "message": "x" * 4001},
        {"session_id": "", "message": "hi"},
        {"session_id": "s" * 129, "message": "hi"},
        {"message": "hi"},
        {"session_id": "s"},
        {"session_id": "s", "message": 123},
    ],
)
def test_validation_errors_are_422_and_not_stored(client, body):
    r = client.post("/chat", json=body)
    assert r.status_code == 422
    assert "detail" in r.json()
    assert client.get("/sessions/s/messages").status_code == 404


def test_message_of_exactly_4000_chars_ok(client):
    assert chat(client, "s", "x" * 4000).status_code == 200


def test_message_is_trimmed(client):
    chat(client, "t", "   padded   ")
    assert client.get("/sessions/t/messages").json()["messages"][0]["content"] == "padded"


def test_malformed_json_is_422(client):
    r = client.post("/chat", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()


def test_unknown_route_is_json(client):
    r = client.get("/nope")
    assert r.status_code == 404 and "detail" in r.json()


def test_cors_allows_configured_origin(client):
    r = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-simulate-failure" in r.headers["access-control-allow-headers"].lower()
