# Lyftr Chat — LLM chatbot with key failover and response caching

A small full-stack AI app: a **React** chat UI, a **FastAPI** backend that talks to the model through
**LiteLLM**, and **SQLite** for per-session history. If an API key is rate-limited or erroring, the backend
silently fails over to the next key; repeated prompts are served from LiteLLM's cache; every reply shows
where it came from, which model answered, latency, and per-request token usage.

## 1. Live URLs

| | URL |
|---|---|
| Frontend | https://lyftr-chatbot-theta.vercel.app |
| Backend  | https://naman-lyftr-chat-api.onrender.com |
| Health   | https://naman-lyftr-chat-api.onrender.com/health |
| Demo video | _add link_ |

> The backend runs on Render's free tier, which sleeps when idle: the first request after a quiet period can take
> ~50 s to wake up. Open the health URL once before testing.

Deploy steps are in [Deploying](#deploying) below.

## 2. Run locally

### With Docker (recommended)

```bash
cp .env.example .env      # then put at least 2 real API keys in LLM_API_KEYS
make up                   # backend  -> http://localhost:8000   frontend -> http://localhost:3000
make down                 # stop (history is kept in the `app_data` volume)
```

Needs Docker with Compose v2. `make logs` tails logs; `make test` runs the backend tests.

### Without Docker

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export LLM_API_KEYS=key-one,key-two                      # Windows (PowerShell): $env:LLM_API_KEYS="key-one,key-two"
export DATABASE_URL=sqlite:///./data/app.db
export ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --port 8000
```

Frontend (Node 20+), in another terminal:

```bash
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev            # http://localhost:3000
```

Tests (no real keys needed — the provider is stubbed, LiteLLM's cache/usage/errors are real):

```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest -q
```

### Configuration (environment variables)

| Variable | Meaning | Default |
|---|---|---|
| `LLM_API_KEYS` | Comma-separated key pool (≥ 2). Ids are `key_1`, `key_2`, … in order | – |
| `LLM_MODEL` | LiteLLM model name used for all keys | `gemini/gemini-3.5-flash-lite` |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` | `sqlite:///./data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | `http://localhost:3000` |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` header | `false` |
| `CACHE_TTL_SECONDS` | Lifetime of a cached response | `3600` |
| `REQUEST_TIMEOUT_SECONDS` | Per-key provider timeout | `30` |
| `MAX_HISTORY_MESSAGES` | Most recent messages sent to the model as context | `40` |
| `DEFAULT_SYSTEM_PROMPT` | System prompt when the client doesn't set one | `You are a helpful, concise assistant.` |
| `API_URL` (frontend container) | Backend URL as seen by the **browser** | `http://localhost:8000` |
| `VITE_API_URL` (frontend build) | Same, for static hosts (Vercel/Netlify) | `http://localhost:8000` |

Get free Gemini keys at <https://aistudio.google.com/apikey>. **Use two keys from different Google
projects/accounts** — keys from the same project share one quota, so failover wouldn't help in practice.
Real keys never go in the repo (`.env` is git-ignored).

**Model availability.** Google retires and restricts Gemini models over time. If every request returns
`503 all providers unavailable` and the backend log shows `NotFoundError`, set `LLM_MODEL` to a model your key can
use. List them with
`https://generativelanguage.googleapis.com/v1beta/models?key=<YOUR_KEY>` (look for `generateContent`), then use
`gemini/<name>`. This project is verified with `gemini/gemini-3.5-flash-lite`.

## 3. Design decisions

**Key fallback.** The key pool is parsed from `LLM_API_KEYS` into ordered ids `key_1…key_n`. For each request the
gateway (`backend/app/llm.py`) tries the keys in order with `litellm.acompletion`; on *any* failure of a key —
429, quota, 5xx, timeout, auth error, or an empty completion — it logs the key **id** (never the key) and moves on.
It returns the id of the key that answered and `fallback_attempts` (how many failed first). If all keys fail, the
API returns `503 {"detail":"all providers unavailable"}`. I used an explicit loop rather than Router fallbacks
because the contract needs deterministic order, exact `key_id`/`fallback_attempts` reporting, and a failure
hook that targets specific keys; LiteLLM still does the provider call, usage accounting and caching.

**Caching.** LiteLLM's built-in in-memory cache (`litellm.cache = Cache(type="local")`). The cache key is
LiteLLM's hash of *model + the full message list* (system prompt + history + new message), so the same first
message in a new session is a hit, while any different message — or a different history/system prompt — is a
miss. A hit returns the identical `reply`, the token usage stored with the original response, `key_id: null`
and `fallback_attempts: 0`, and skips the network entirely (single-digit milliseconds vs. hundreds). I write
the response into LiteLLM's cache explicitly right after a live answer (instead of relying on LiteLLM's
background write) so an immediately repeated request is always a hit. The cache is in-memory, so it resets
when the backend restarts; session history does not.

**SQLite session storage.** `messages` follows the required schema (plus an index), and a small `sessions`
table keeps each session's system prompt. The schema is created on startup. The DB path comes from
`DATABASE_URL` and lives on a Docker named volume (`/data`), so history survives container restarts. History is
written **only after** a successful reply, in one transaction (user + assistant message), so failed requests
(422/503) never appear in history. The client sends only the new message; the server loads history and sends
the system prompt + recent history + new message to the model. A per-session lock keeps concurrent messages in
the same chat ordered.

**Failure simulation.** `X-Simulate-Failure: key_1=429,key_2=500` / `*=429` (statuses 429, 500, 502, 503) makes
those keys fail *inside that one request* by raising the same LiteLLM exception types real failures raise, so it
exercises the real failover path without burning quota. It is honoured only when
`ENABLE_FAILURE_SIMULATION=true`. A request that carries simulated failures skips the cache *read* so the
failover is always visible even for a message that was cached earlier.

**Errors.** Every error is JSON with a `detail` (custom handlers for validation, HTTP and unexpected errors; no
stack traces or HTML). Invalid input → 422, all keys failed → 503, unknown session → 404.

**Security.** API keys exist only in environment variables, are never returned or logged, and are never in the
repo. CORS is restricted to `ALLOWED_ORIGINS`. The backend container runs as a non-root user.

**Frontend.** Plain React + Vite. The API URL is runtime config in Docker (`API_URL` → `config.js` at container
start, so no rebuild) and build-time (`VITE_API_URL`) on static hosts. A new session id is generated on every
page load. Errors render as a readable message in the chat, never a blank screen.

## 4. Verification

Replace `$API` with `http://localhost:8000` or your deployed backend.

**Per-message metadata in the UI** — each assistant reply shows `resolution_source`, model, latency (ms), and
input/output tokens for *that request only*.

**Cache hit** (the UI also shows `LLM_CACHE` + a much lower latency):

```bash
curl -s $API/chat -H 'content-type: application/json' -d '{"session_id":"a1","message":"What is the capital of France?"}'
curl -s $API/chat -H 'content-type: application/json' -d '{"session_id":"a2","message":"What is the capital of France?"}'
# 1st: "resolution_source":"LIVE_INFERENCE"   2nd: "resolution_source":"LLM_CACHE","key_id":null  (same reply & tokens)
```

**Failover** (needs `ENABLE_FAILURE_SIMULATION=true`; the UI has a "Demo: simulate a key failure" selector too):

```bash
curl -s $API/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"f1","message":"failover demo"}'          # 200, "key_id":"key_2","fallback_attempts":1
curl -s -i $API/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"f2","message":"all keys down"}'           # 503 {"detail":"all providers unavailable"}
curl -s -i $API/sessions/f2/messages                           # 404 — the failed request was not stored
```

**Multi-turn + history:**

```bash
curl -s $API/chat -H 'content-type: application/json' -d '{"session_id":"m1","message":"My name is Sam."}'
curl -s $API/chat -H 'content-type: application/json' -d '{"session_id":"m1","message":"What is my name?"}'
curl -s $API/sessions/m1/messages        # oldest first, assistant messages carry their metadata
```

**Persistence:** `make down && make up`, then `curl $API/sessions/m1/messages` still returns the history.

**Validation:** `curl -s -i $API/chat -H 'content-type: application/json' -d '{"session_id":"x","message":"   "}'` → 422.

**Automated tests:** `make test` (51 tests: contract, caching, failover, simulation header, validation,
persistence, CORS, no key leakage).

## 5. Setup Used

- Editor / agent: Claude Code (Anthropic), running in a cloud session, used to write the code and tests.
- Backend: Python, FastAPI, LiteLLM, SQLite (stdlib `sqlite3`), pytest.
- Frontend: React 18, Vite.
- Infra: Docker (multi-stage), Docker Compose, GNU Make, nginx (static serving).
- Model: Google Gemini via LiteLLM (`gemini/gemini-3.5-flash-lite`, configurable with `LLM_MODEL`).

## 6. Bonus features

- **Demo selector in the UI** to trigger simulated key failures on demand (handy for the demo video).
- **Extra metadata chips** showing which key answered (`key_1`/`key_2`/none for cache) and the fallback count.
- **Per-session lock** so rapid-fire messages in one chat are stored in order.
- **History window** (`MAX_HISTORY_MESSAGES`) so very long chats don't overflow the model's context.
- **Empty-completion failover**: a blank model reply is treated as a key failure, not returned to the user.
- **Runtime frontend config** (`API_URL`) — one Docker image works against any backend URL.
- **Container healthchecks** for both services.

## Deploying

**Backend → Render (Docker web service)**

1. New → Web Service → connect this repo → *Root Directory* `backend`, *Runtime* Docker.
2. Environment variables: `LLM_API_KEYS`, `ALLOWED_ORIGINS` (your frontend URL, no trailing slash),
   `ENABLE_FAILURE_SIMULATION=true`, `DATABASE_URL=sqlite:////data/app.db`.
3. For history to survive redeploys, attach a **Disk** mounted at `/data` (requires a paid instance). On the free
   tier the disk is ephemeral, so history resets when the instance is rebuilt or restarts.
4. Check `<backend>/health`.

**Frontend → Vercel (or Netlify)**

1. Import this repo → *Root Directory* `frontend` (Vite is auto-detected; build `npm run build`, output `dist`).
2. Environment variable: `VITE_API_URL=https://<your-backend>` (no trailing slash), then deploy.
3. Add the resulting URL to the backend's `ALLOWED_ORIGINS` and redeploy the backend.

Fill in the URLs in section 1 when done.
