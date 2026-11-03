"""UGA build platform — HTTP API + UI host."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, orchestrator, runner
from .agent import AgentClient
from .store import store

api = FastAPI(title="UGA Build Platform", version="1.0.0")
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateAppRequest(BaseModel):
    name: str = Field(default="", max_length=80)
    prompt: str = Field(min_length=1)


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1)


@api.get("/api/health")
def health() -> dict:
    client = AgentClient()
    return {
        "ok": True,
        "agent_server": client.healthy(),
        "agent_model": config.AGENT_MODEL,
        "urls": config.public_urls(),
    }


@api.get("/api/apps")
def list_apps() -> dict:
    return {"apps": [a.to_dict() for a in store.list()]}


@api.post("/api/apps")
def create_app(payload: CreateAppRequest) -> dict:
    name = payload.name.strip() or payload.prompt.strip().split("\n")[0][:60]
    record = store.create(name, payload.prompt)
    orchestrator.start_build(record.id, payload.prompt)
    return record.to_dict()


@api.get("/api/apps/{app_id}")
def get_app(app_id: str) -> dict:
    record = store.get(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    data = record.to_dict()
    data["log"] = runner.tail_log(record)
    return data


@api.post("/api/apps/{app_id}/prompt")
def prompt_app(app_id: str, payload: PromptRequest) -> dict:
    record = store.get(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    orchestrator.start_build(app_id, payload.prompt, continue_conversation=True)
    return store.get(app_id).to_dict()


@api.post("/api/apps/{app_id}/rebuild")
def rebuild_app(app_id: str) -> dict:
    if store.get(app_id) is None:
        raise HTTPException(404, "app not found")
    orchestrator.rebuild(app_id)
    return store.get(app_id).to_dict()


@api.post("/api/apps/{app_id}/start")
def start_app(app_id: str) -> dict:
    record = orchestrator.start_app(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    return record.to_dict()


@api.post("/api/apps/{app_id}/stop")
def stop_app(app_id: str) -> dict:
    record = orchestrator.stop_app(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    return record.to_dict()


@api.delete("/api/apps/{app_id}")
def delete_app(app_id: str) -> dict:
    record = store.get(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    runner.stop(record)
    runner.cleanup_workspace(record)
    store.delete(app_id)
    return {"deleted": app_id}


@api.get("/api/apps/{app_id}/files")
def app_files(app_id: str) -> dict:
    record = store.get(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    return {"files": runner.list_files(record.workspace)}


@api.get("/api/apps/{app_id}/log")
def app_log(app_id: str) -> dict:
    record = store.get(app_id)
    if record is None:
        raise HTTPException(404, "app not found")
    return {"log": runner.tail_log(record)}


@api.get("/api/apps/{app_id}/events")
async def app_events(app_id: str) -> StreamingResponse:
    if store.get(app_id) is None:
        raise HTTPException(404, "app not found")

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
