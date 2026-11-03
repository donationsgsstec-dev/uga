"""Entry point: serve the UGA console (12000) and the preview proxy (12001)."""

from __future__ import annotations

import threading

import uvicorn

from backend import config


def _serve_preview() -> None:
    uvicorn.run(
        "backend.proxy:app",
        host=config.HOST,
        port=config.PREVIEW_PORT,
        log_level="warning",
        access_log=False,
    )


def main() -> None:
    # In single-port mode the preview proxy is mounted inside the console app
    # (see backend.main), so we don't bind a second public port.
    if not config.SINGLE_PORT:
        threading.Thread(target=_serve_preview, daemon=True).start()
    uvicorn.run("backend.main:api", host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
