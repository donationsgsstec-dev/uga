"""UGA build platform — HTTP API + UI host."""

from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, orchestrator, runner
from .agent import AgentClient, AgentServerError
from .auth import (
    ALLOW_FREE,
    DEFAULT_PROVIDER,
    FREE_MODEL,
    SESSION_COOKIE,
    User,
    auth_store,
    fingerprint,
)
from .store import store

api = FastAPI(title="UGA Build Platform", version="1.1.0")
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Auth plumbing
# --------------------------------------------------------------------------- #
def current_user(request: Request) -> User:
    resolved = auth_store.resolve_session(request.cookies.get(SESSION_COOKIE))
    if resolved is None:
        raise HTTPException(401, "not signed in")
    return resolved[1]


def set_session_cookie(response: Response, request: Request, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=60 * 60 * 24 * 14,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


class ApiKeyLogin(BaseModel):
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)
    provider: str = ""
    base_url: str | None = None


class DeviceStartRequest(BaseModel):
    model: str = Field(default="", max_length=120)


class DevicePollRequest(BaseModel):
    attempt_id: str = Field(min_length=1)


@api.get("/api/auth/methods")
def auth_methods() -> dict:
    """What sign-in options this deployment offers."""
    client = AgentClient()
    providers = []
    if client.healthy():
        try:
            providers = client.list_providers()
        except AgentServerError:
            providers = []
    return {
        "free": ALLOW_FREE,
        "free_model": config.resolve_model(FREE_MODEL),
        "forced_model": config.FORCED_MODEL,
        "demo": ALLOW_FREE,
        "default_provider": DEFAULT_PROVIDER,
        "subscription_vendor": "openai",
        "providers": providers,
    }


@api.get("/api/auth/me")
def auth_me(request: Request, response: Response) -> dict:
    resolved = auth_store.resolve_session(request.cookies.get(SESSION_COOKIE))
    if resolved is not None:
        return {"authenticated": True, "user": resolved[1].public()}
    # Free model: sign everyone in automatically so there is no login wall.
    if ALLOW_FREE:
        user = auth_store.get_or_create_platform_user()
        session = auth_store.create_session(user.id)
        set_session_cookie(response, request, session.id)
        return {"authenticated": True, "user": user.public(), "auto": True}
    return {"authenticated": False}


@api.post("/api/auth/login/api-key")
def login_api_key(payload: ApiKeyLogin, request: Request, response: Response) -> dict:
    """Sign in with the user's own provider API key.

    The key is validated against the agent server and stored there as a provider
    connection; UGA keeps only the connection id and a fingerprint.
    """
    client = AgentClient()
    if not client.healthy():
        raise HTTPException(503, "the local agent server is not reachable")

    # When a model is pinned, authenticate and run on it — never the client's.
    model = config.resolve_model(payload.model)
    provider = (payload.provider or _infer_provider(model)).strip()
    llm = {"model": model, "api_key": payload.api_key}
    if payload.base_url:
        llm["base_url"] = payload.base_url

    valid, error = client.validate_llm(llm)
    if not valid:
        raise HTTPException(400, error or "the provider rejected this credential")

    # Reuse the signed-in user's slot when re-keying; otherwise start fresh.
    resolved = auth_store.resolve_session(request.cookies.get(SESSION_COOKIE))
    existing = resolved[1] if resolved else None

    user = auth_store.upsert_user(
        user_id=existing.id if existing else None,
        token_source="api_key",
        model=model,
        provider=provider,
        base_url=payload.base_url,
        key_fingerprint=fingerprint(payload.api_key),
    )
    if existing and existing.provider_connection_id:
        client.delete_provider_connection(existing.provider_connection_id)
    try:
        connection_id = client.create_provider_connection(
            display_name=f"uga:{user.id}",
            provider=provider,
            api_key=payload.api_key,
            base_url=payload.base_url,
        )
    except AgentServerError as exc:
        raise HTTPException(502, f"could not store the credential: {exc}") from exc
    user = auth_store.upsert_user(
        user_id=user.id,
        token_source="api_key",
        model=model,
        provider=provider,
        base_url=payload.base_url,
        provider_connection_id=connection_id,
        key_fingerprint=fingerprint(payload.api_key),
    )

    session = auth_store.create_session(user.id)
    set_session_cookie(response, request, session.id)
    return {"user": user.public()}


@api.post("/api/auth/login/free")
def login_free(request: Request, response: Response) -> dict:
    """Continue on the shared, free platform model (no personal credential)."""
    if not ALLOW_FREE:
        raise HTTPException(403, "free sign-in is disabled on this deployment")
    user = auth_store.get_or_create_platform_user()
    session = auth_store.create_session(user.id)
    set_session_cookie(response, request, session.id)
    return {"user": user.public()}


@api.post("/api/auth/login/demo")
def login_demo(request: Request, response: Response) -> dict:
    """Alias of /login/free, kept for backwards compatibility."""
    return login_free(request, response)


@api.post("/api/auth/device/start")
def device_start(payload: DeviceStartRequest) -> dict:
    """Begin an OAuth-style device login against the user's OpenAI account."""
    client = AgentClient()
    try:
        start = client.subscription_device_start()
    except AgentServerError as exc:
        raise HTTPException(502, str(exc)) from exc
    attempt_id = auth_store.start_attempt(
        start["device_code"], model=config.resolve_model(payload.model)
    )
    return {
        "attempt_id": attempt_id,
        "user_code": start.get("user_code", ""),
        "verification_uri": start.get("verification_uri", ""),
        "verification_uri_complete": start.get("verification_uri_complete"),
        "expires_at": start.get("expires_at"),
        "interval_seconds": start.get("interval_seconds", 5),
    }


@api.post("/api/auth/device/poll")
def device_poll(payload: DevicePollRequest, request: Request, response: Response) -> dict:
    attempt = auth_store.get_attempt(payload.attempt_id)
    if attempt is None:
        raise HTTPException(404, "this sign-in attempt has expired")

    client = AgentClient()
    result = client.subscription_device_poll(attempt["device_code"])
    if not result.get("connected"):
        return {"status": "pending"}

    user = auth_store.upsert_user(
        user_id=None,
        token_source="subscription",
        model=config.resolve_model(attempt.get("model")),
        provider="openai",
        account_email=result.get("account_email") or "",
    )
    auth_store.finish_attempt(payload.attempt_id)
    session = auth_store.create_session(user.id)
    set_session_cookie(response, request, session.id)
    return {"status": "connected", "user": user.public()}


@api.post("/api/auth/device/cancel")
def device_cancel(payload: DevicePollRequest) -> dict:
    auth_store.finish_attempt(payload.attempt_id)
    return {"cancelled": True}


@api.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    auth_store.destroy_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


def _infer_provider(model: str) -> str:
    """Guess a litellm provider prefix from the model name."""
    if "/" in model:
        return model.split("/", 1)[0]
    lowered = model.lower()
    if lowered.startswith("gpt") or lowered.startswith("o1") or lowered.startswith("o3"):
        return "openai"
    if lowered.startswith("claude"):
        return "anthropic"
    if lowered.startswith("gemini"):
        return "gemini"
    return DEFAULT_PROVIDER


# --------------------------------------------------------------------------- #
# App API (owner-scoped)
# --------------------------------------------------------------------------- #
class CreateAppRequest(BaseModel):
    name: str = Field(default="", max_length=80)
    prompt: str = Field(min_length=1)


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1)


def owned_app(app_id: str, user: User):
    record = store.get(app_id)
    # Ownerless records predate sign-in and stay accessible to any signed-in user.
    if record is None or record.owner_id not in (user.id, ""):
        raise HTTPException(404, "app not found")
    return record


@api.get("/api/health")
def health() -> dict:
    client = AgentClient()
    return {
        "ok": True,
        "agent_server": client.healthy(),
        "agent_model": config.AGENT_MODEL,
        "auth_required": True,
        "urls": config.public_urls(),
    }


@api.get("/api/apps")
def list_apps(user: User = Depends(current_user)) -> dict:
    return {"apps": [a.to_dict() for a in store.list_for_owner(user.id)]}


@api.post("/api/apps")
def create_app(payload: CreateAppRequest, user: User = Depends(current_user)) -> dict:
    name = payload.name.strip() or payload.prompt.strip().split("\n")[0][:60]
    record = store.create(name, payload.prompt, owner_id=user.id)
    orchestrator.start_build(record.id, payload.prompt)
    return record.to_dict()


@api.get("/api/apps/{app_id}")
def get_app(app_id: str, user: User = Depends(current_user)) -> dict:
    record = owned_app(app_id, user)
    data = record.to_dict()
    data["log"] = runner.tail_log(record)
    return data


@api.post("/api/apps/{app_id}/prompt")
def prompt_app(app_id: str, payload: PromptRequest, user: User = Depends(current_user)) -> dict:
    owned_app(app_id, user)
    orchestrator.start_build(app_id, payload.prompt, continue_conversation=True)
    return store.get(app_id).to_dict()


@api.post("/api/apps/{app_id}/rebuild")
def rebuild_app(app_id: str, user: User = Depends(current_user)) -> dict:
    owned_app(app_id, user)
    orchestrator.rebuild(app_id)
    return store.get(app_id).to_dict()


@api.post("/api/apps/{app_id}/start")
def start_app(app_id: str, user: User = Depends(current_user)) -> dict:
    owned_app(app_id, user)
    record = orchestrator.start_app(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    return record.to_dict()


@api.post("/api/apps/{app_id}/stop")
def stop_app(app_id: str, user: User = Depends(current_user)) -> dict:
    owned_app(app_id, user)
    record = orchestrator.stop_app(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    return record.to_dict()


@api.delete("/api/apps/{app_id}")
def delete_app(app_id: str, user: User = Depends(current_user)) -> dict:
    record = owned_app(app_id, user)
    runner.stop(record)
    runner.cleanup_workspace(record)
    store.delete(app_id)
    return {"deleted": app_id}


@api.get("/api/apps/{app_id}/files")
def app_files(app_id: str, user: User = Depends(current_user)) -> dict:
    record = owned_app(app_id, user)
    return {"files": runner.list_files(record.workspace)}


@api.get("/api/apps/{app_id}/log")
def app_log(app_id: str, user: User = Depends(current_user)) -> dict:
    record = owned_app(app_id, user)
    return {"log": runner.tail_log(record)}


@api.get("/api/apps/{app_id}/events")
async def app_events(app_id: str, user: User = Depends(current_user)) -> StreamingResponse:
    owned_app(app_id, user)

    async def generate():
        async for event in orchestrator.hub.subscribe(app_id):
            yield orchestrator.sse_format(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@api.on_event("startup")
async def _on_startup() -> None:
    orchestrator.hub.bind_loop(asyncio.get_running_loop())
    orchestrator.reconcile()


# --------------------------------------------------------------------------- #
# Preview (single-port deployments serve it from this same app/origin)
# --------------------------------------------------------------------------- #
if config.SINGLE_PORT:
    from .proxy import router as preview_router

    api.include_router(preview_router)


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
if config.FRONTEND_DIST.is_dir():
    api.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @api.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = (config.FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and str(candidate).startswith(str(config.FRONTEND_DIST)):
            return FileResponse(candidate)
        return FileResponse(config.FRONTEND_DIST / "index.html")

else:

    @api.get("/")
    def placeholder() -> HTMLResponse:
        return HTMLResponse(
            """<!doctype html><html><body style="font-family:system-ui;background:#0b1020;color:#eee;
            padding:40px"><h1>UGA backend is running</h1>
            <p>The frontend bundle is not built yet. Run <code>cd uga/frontend &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>
            <p>API docs: <a style="color:#8fb4ff" href="/docs">/docs</a></p></body></html>"""
        )
