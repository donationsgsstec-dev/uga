"""Preview proxy served on the second public port.

Every running app is reachable at ``/p/<app_id>/`` on the preview host. Static
apps are served straight from the workspace; apps that run their own server are
reverse-proxied to the app's loopback port.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import runner
from .store import store

app = FastAPI(title="UGA Preview", docs_url=None, redoc_url=None)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;
   background:#0b1020;color:#e6e9f5;font:15px/1.6 ui-sans-serif,system-ui,sans-serif}}
 .card{{max-width:560px;padding:36px 40px;border-radius:18px;
   background:#141a30;border:1px solid #26304f;box-shadow:0 20px 60px rgba(0,0,0,.45)}}
 h1{{margin:0 0 10px;font-size:20px}} p{{margin:0 0 8px;color:#9aa6c8}}
 code{{background:#0b1020;padding:2px 7px;border-radius:6px;color:#8fb4ff}}
 a{{color:#8fb4ff}}
</style></head><body><div class="card"><h1>{title}</h1>{body}</div></body></html>""",
        status_code=status,
    )


def _resolve_static(workspace: Path, rel: str) -> Path | None:
    root = runner._static_root(workspace)
    if root is None:
        return None
    rel = rel.lstrip("/")
    candidate = (root / rel).resolve() if rel else (root / "index.html").resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if candidate.is_dir():
        for name in ("index.html", "index.htm"):
            if (candidate / name).is_file():
                return candidate / name
        return None
    if candidate.is_file():
        return candidate
    # SPA fallback
    index = root / "index.html"
    return index if index.is_file() else None


async def _proxy(port: int, subpath: str, request: Request) -> Response:
    url = request.url.replace(
        scheme="http", netloc=f"127.0.0.1:{port}", path="/" + subpath.lstrip("/")
    )
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    body = await request.body()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        upstream = await client.request(request.method, str(url), headers=headers, content=body)
    out_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )


@app.get("/")
def index() -> HTMLResponse:
    apps = [a for a in store.list() if a.status == "running"]
    if len(apps) == 1:
        return _page(
            "UGA preview",
            f'<p>One app is running: <a href="/p/{apps[0].id}/">{apps[0].name}</a></p>',
        )
    rows = "".join(
        f'<p><a href="/p/{a.id}/">{a.name}</a> <code>{a.mode}</code></p>' for a in apps
    ) or "<p>No apps are running yet. Build one in the UGA console.</p>"
    return _page("UGA preview", rows)


@app.api_route("/p/{app_id}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@app.api_route(
    "/p/{app_id}/{subpath:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def preview(app_id: str, request: Request, subpath: str = "") -> Response:
    record = store.get(app_id)
    if record is None:
        return _page("Unknown app", f"<p>No app with id <code>{app_id}</code>.</p>", 404)
    if record.status != "running":
        return _page(
            "App not running",
            f"<p><b>{record.name}</b> is currently <code>{record.status}</code>.</p>"
            "<p>Start it from the UGA console.</p>",
            503,
        )

    if record.internal_port:
        return await _proxy(record.internal_port, subpath, request)

    target = _resolve_static(record.workspace, subpath)
    if target is None:
        return _page(
            "Nothing to serve",
            f"<p><b>{record.name}</b> has no <code>index.html</code> to serve.</p>",
            404,
        )
    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
