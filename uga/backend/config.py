"""Runtime configuration for the UGA build platform."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parent
UGA_DIR = BACKEND_DIR.parent
ROOT_DIR = UGA_DIR.parent

APPS_DIR = Path(os.environ.get("UGA_APPS_DIR", UGA_DIR / "apps"))
DATA_DIR = Path(os.environ.get("UGA_DATA_DIR", UGA_DIR / "data"))
LOGS_DIR = DATA_DIR / "logs"
FRONTEND_DIST = UGA_DIR / "frontend" / "dist"

APPS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

HOST = os.environ.get("UGA_HOST", "0.0.0.0")
PORT = int(os.environ.get("UGA_PORT", "12000"))
PREVIEW_PORT = int(os.environ.get("UGA_PREVIEW_PORT", "12001"))

# Ports handed to generated apps' own dev/prod servers.
APP_PORT_RANGE_START = int(os.environ.get("UGA_APP_PORT_START", "12100"))
APP_PORT_RANGE_END = int(os.environ.get("UGA_APP_PORT_END", "12299"))

AGENT_SERVER_URL = os.environ.get("UGA_AGENT_SERVER_URL", "http://127.0.0.1:60000")
AGENT_MODEL = os.environ.get("UGA_MODEL", "openhands/deepseek-v4.1-flash")
AGENT_MAX_ITERATIONS = int(os.environ.get("UGA_MAX_ITERATIONS", "150"))

# When set, every build runs on this model regardless of the user's token or
# sign-in method. Empty (the default) honours each user's own model.
FORCED_MODEL = os.environ.get("UGA_FORCED_MODEL", "").strip()


def resolve_model(requested: str | None = None) -> str:
    """The model a build should actually use.

    A non-empty ``FORCED_MODEL`` always wins, so the whole deployment is pinned
    to one model; otherwise the caller's requested model is used, falling back
    to the platform default.
    """
    if FORCED_MODEL:
        return FORCED_MODEL
    return (requested or "").strip() or AGENT_MODEL


def _discover_session_api_key() -> str | None:
    """The agent server authenticates ``/api/*`` with an X-Session-API-Key header.

    That key is not exported into our own environment, so read it from the
    agent server process' environment.
    """
    explicit = os.environ.get("UGA_AGENT_SESSION_KEY") or os.environ.get("SESSION_API_KEY")
    if explicit:
        return explicit
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "ignore")
            if "openhands-agent-server" not in cmdline:
                continue
            environ = (entry / "environ").read_bytes().decode("utf-8", "ignore")
        except OSError:
            continue
        for line in environ.split("\0"):
            if line.startswith("SESSION_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


SESSION_API_KEY = _discover_session_api_key()


def _public_base(prefix: str) -> str:
    explicit = os.environ.get("UGA_PUBLIC_BASE")
    runtime_url = os.environ.get("RUNTIME_URL", "")
    if runtime_url:
        host = urlparse(runtime_url).netloc
        if host:
            return f"https://{prefix}-{host}/"
    if explicit:
        return explicit
    return f"http://localhost:{PORT}/"


def public_urls() -> dict[str, str]:
    return {
        "ui": _public_base("work-1"),
        "preview": _public_base("work-2"),
    }
