"""FastAPI application: /health, /chat, /sessions/{id}/messages."""
from __future__ import annotations

import asyncio
import logging
import weakref
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .db import Database
from .llm import AllProvidersUnavailable, LLMGateway, parse_simulation
from .schemas import ChatRequest, ChatResponse, Metadata

log = logging.getLogger("chatbot")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.database_url)
    gateway = LLMGateway(settings)
    # One lock per active session so concurrent messages in the same chat stay ordered.
    session_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init()  # schema is created automatically on startup
        await gateway.warmup()
        log.info("started: %d key(s), model=%s", len(gateway.keys), gateway.model)
        yield

    app = FastAPI(title="Lyftr LLM Chatbot", version="1.0.0", lifespan=lifespan)
    app.state.settings, app.state.db, app.state.gateway = settings, db, gateway

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- errors: always JSON with a `detail`, never a stack trace or HTML -------------
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        detail = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", "invalid"), "type": e.get("type", "error")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    # ---- routes ---------------------------------------------------------------------
    @app.get("/health")
    async def health():
        db_ok = await asyncio.to_thread(db.ping)
        keys = len(gateway.keys)
        body = {"status": "ok", "keys_configured": keys, "cache_enabled": gateway.cache_enabled}
        if not db_ok or keys == 0:
            body["status"] = "error"
            body["detail"] = "database unreachable" if not db_ok else "no API keys configured"
            return JSONResponse(status_code=503, content=body)
        return body

    @app.post("/chat", response_model=ChatResponse)
    async def chat(
        req: ChatRequest,
        x_simulate_failure: Optional[str] = Header(default=None, alias="X-Simulate-Failure"),
    ):
        simulate = parse_simulation(x_simulate_failure) if settings.enable_failure_simulation else {}

        lock = session_locks.get(req.session_id)
        if lock is None:
            lock = session_locks.setdefault(req.session_id, asyncio.Lock())
        async with lock:
            history = await asyncio.to_thread(db.get_history, req.session_id, settings.max_history_messages)
            stored_prompt = await asyncio.to_thread(db.get_system_prompt, req.session_id)
            system_prompt = req.system_prompt or stored_prompt or settings.default_system_prompt

            messages = [{"role": "system", "content": system_prompt}]
            messages += [{"role": m["role"], "content": m["content"]} for m in history]
            messages.append({"role": "user", "content": req.message})

            try:
                result = await gateway.complete(messages, simulate)
            except AllProvidersUnavailable:
                # Nothing is persisted for a failed request.
                return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

            await asyncio.to_thread(
                db.save_exchange, req.session_id, req.system_prompt, req.message, result.reply, result.metadata()
            )

        return ChatResponse(
            session_id=req.session_id,
            reply=result.reply,
            metadata=Metadata(**result.metadata()),
        )

    @app.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str):
        if not await asyncio.to_thread(db.session_exists, session_id):
            return JSONResponse(status_code=404, content={"detail": "session not found"})
        history = await asyncio.to_thread(db.get_history, session_id)
        # `metadata` is only present on assistant messages.
        messages = [
            {"role": m["role"], "content": m["content"], **({"metadata": m["metadata"]} if m["metadata"] else {})}
            for m in history
        ]
        return {"session_id": session_id, "messages": messages}

    return app


app = create_app()
