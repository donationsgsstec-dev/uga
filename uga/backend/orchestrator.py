"""Coordinates the agent -> build -> run pipeline and broadcasts progress."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from typing import Any

from . import agent as agent_mod
from . import config, runner
from .auth import auth_store
from .store import AppRecord, store

HISTORY_LIMIT = 600


class EventHub:
    """Fan-out of progress events to any number of SSE subscribers."""

    def __init__(self) -> None:
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.RLock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def history(self, app_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(app_id, ()))

    def publish(self, app_id: str, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        with self._lock:
            self._history.setdefault(app_id, deque(maxlen=HISTORY_LIMIT)).append(event)
            queues = list(self._subscribers.get(app_id, ()))
        for queue in queues:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(queue.put_nowait, event)

    async def subscribe(self, app_id: str) -> asyncio.AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.setdefault(app_id, set()).add(queue)
        try:
            for event in self.history(app_id):
                yield event
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield {"kind": "ping", "ts": time.time()}
        finally:
            with self._lock:
                self._subscribers.get(app_id, set()).discard(queue)


hub = EventHub()


def emit(app_id: str, kind: str, text: str, **extra: Any) -> None:
    hub.publish(app_id, {"kind": kind, "text": text, **extra})


def _persist(app_id: str, kind: str, text: str) -> None:
    record = store.get(app_id)
    if record is None:
        return
    line = f"[{time.strftime('%H:%M:%S')}] {kind}: {text}"
    with (record.base_dir / "logs" / "pipeline.log").open("a") as fh:
        fh.write(line + "\n")


def log(app_id: str, kind: str, text: str, **extra: Any) -> None:
    emit(app_id, kind, text, **extra)
    _persist(app_id, kind, text)


def build_prompt(name: str, user_prompt: str) -> str:
    return f"""You are building a web application inside the current working directory.

App name: {name}

What the user wants:
{user_prompt}

Requirements:
- Build a complete, working, self-contained web application in THIS directory.
- Prefer a no-build-step static app (plain index.html + CSS + JS) unless the user's
  request clearly needs a backend or a framework build. Keep it simple and robust.
- Use RELATIVE asset paths (e.g. "./app.js", "./styles.css"), never absolute paths.
- The app must run from a static file server with no extra configuration.
- If you must use Node, make sure `npm run build` (if present) succeeds, or provide a
  `start` script that serves on the port from the PORT environment variable.
- If you use Python, read the port from the PORT environment variable.
- Make the UI modern, polished and responsive.
- Verify your work: open/read the files you created and check the logic is correct.
- When done, call the finish tool with a short summary of what you built and how to use it.
"""


def _public_url(record: AppRecord) -> str:
    base = config.public_urls()["preview"].rstrip("/")
    return f"{base}{record.preview_path}"


def start_build(app_id: str, prompt: str, *, continue_conversation: bool = False) -> None:
    store.update(app_id, status="building", stage="agent is writing code", last_error="")
    thread = threading.Thread(
        target=_run_build, args=(app_id, prompt, continue_conversation), daemon=True
    )
    thread.start()


def _run_build(app_id: str, prompt: str, continue_conversation: bool) -> None:
    record = store.get(app_id)
    if record is None:
        return
    client = agent_mod.AgentClient()

    # The build runs on the app owner's own token; fall back to the platform
    # default profile for demo/anonymous owners.
    owner = auth_store.get_user(record.owner_id) if record.owner_id else None
    llm = owner.llm_settings() if owner else None

    try:
        if not client.healthy():
            raise runner.RunnerError("local agent server is not reachable")

        if continue_conversation and record.conversation_id:
            store.update(app_id, status="building", stage="agent is working", last_error="")
            log(app_id, "stage", f"Continuing build: {prompt}")
            client.send_message(record.conversation_id, prompt, run=True)
            conversation_id = record.conversation_id
        else:
            store.update(app_id, status="building", stage="agent is writing code", last_error="")
            source = owner.token_source if owner else "platform"
            log(app_id, "stage", f"Starting build on {source} token: {prompt}")
            conversation_id = client.start_conversation(
                str(record.workspace), build_prompt(record.name, prompt), llm=llm
            )
            store.update(app_id, conversation_id=conversation_id)
            log(app_id, "info", f"Agent conversation {conversation_id} started")

        _stream_agent(client, app_id, conversation_id)

        files = runner.list_files(record.workspace)
        store.update(app_id, files=files)
        log(app_id, "info", f"Agent produced {len(files)} file(s)")

        store.update(app_id, status="deploying", stage="compiling")
        log(app_id, "stage", "Compiling / preparing app")

        ok, mode, build_log = runner.build(record)
        store.update(app_id, mode=mode, build_log=build_log[-20000:])
        if not ok:
            store.update(app_id, status="error", stage="build failed", last_error="build failed")
            log(app_id, "error", "Build failed — see the build log")
            emit(app_id, "done", "build failed", status="error")
            return
        log(app_id, "info", f"Build ok (mode: {mode})")

        store.update(app_id, status="deploying", stage="starting server")
        log(app_id, "stage", "Starting app")
        runner.stop(record)
        result = runner.start(record)
        store.update(app_id, **result)
        log(app_id, "info", result.get("stage", "running"))

        public_url = _public_url(record)
        store.update(app_id, public_url=public_url, status="running", stage="running")
        log(app_id, "success", f"App is live at {public_url}")
        emit(app_id, "done", "running", status="running", url=public_url)

    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        store.update(app_id, status="error", stage="error", last_error=str(exc))
        log(app_id, "error", f"{type(exc).__name__}: {exc}")
        emit(app_id, "done", "error", status="error")


def _stream_agent(client: agent_mod.AgentClient, app_id: str, conversation_id: str) -> None:
    seen: set[str] = set()
    idle_rounds = 0
    while True:
        for event in client.events(conversation_id):
            event_id = event.get("id", "")
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            idle_rounds = 0
            mapped = agent_mod.normalise(event)
            if mapped:
                log(app_id, mapped.kind, mapped.title, tool=mapped.tool, detail=mapped.detail)
        status = client.status(conversation_id)
        if status in ("finished", "error", "stopped"):
            log(app_id, "info", f"Agent {status}")
            return
        idle_rounds += 1
        if idle_rounds > 2400:  # ~2h safety valve
            log(app_id, "error", "agent timed out")
            return
        time.sleep(1.2)


def rebuild(app_id: str) -> None:
    """Recompile and restart without involving the agent."""
    record = store.get(app_id)
    if record is None:
        return
    runner.stop(record)
    store.update(app_id, status="deploying", stage="compiling")
    log(app_id, "stage", "Recompiling app")
    ok, mode, build_log = runner.build(record)
    store.update(app_id, mode=mode, build_log=build_log[-20000:])
    if not ok:
        store.update(app_id, status="error", stage="build failed", last_error="build failed")
        log(app_id, "error", "Build failed")
        return
    try:
        result = runner.start(record)
    except Exception as exc:  # noqa: BLE001
        store.update(app_id, status="error", stage="error", last_error=str(exc))
        log(app_id, "error", str(exc))
        return
    store.update(app_id, **result)
    public_url = _public_url(record)
    store.update(app_id, status="running", stage="running", public_url=public_url)
    log(app_id, "success", f"App is live at {public_url}")


def start_app(app_id: str) -> AppRecord | None:
    record = store.get(app_id)
    if record is None:
        return None
    try:
        result = runner.start(record)
    except Exception as exc:  # noqa: BLE001
        store.update(app_id, status="error", stage="error", last_error=str(exc))
        log(app_id, "error", str(exc))
        return store.get(app_id)
    store.update(app_id, **result)
    public_url = _public_url(record)
    store.update(app_id, public_url=public_url)
    log(app_id, "success", f"App started at {public_url}")
    return store.get(app_id)


def stop_app(app_id: str) -> AppRecord | None:
    record = store.get(app_id)
    if record is None:
        return None
    runner.stop(record)
    store.update(app_id, status="stopped", stage="stopped", pid=None)
    log(app_id, "info", "App stopped")
    return store.get(app_id)


def sse_format(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def reconcile() -> None:
    """Repair records left in a transient state by a previous server run."""
    for record in store.list():
        if record.status in ("building", "deploying"):
            store.update(
                record.id,
                status="error",
                stage="interrupted",
                last_error="The platform restarted while this app was building. Press Rebuild.",
            )
        elif record.status == "running" and record.pid and not _alive(record.pid):
            store.update(record.id, status="stopped", stage="stopped", pid=None)
