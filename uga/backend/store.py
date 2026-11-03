"""Persistent app registry backed by a single JSON file."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import config


def _slug(text: str, limit: int = 40) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text.strip()]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:limit] or "app"


def new_app_id(name: str) -> str:
    return f"{_slug(name, 24)}-{uuid.uuid4().hex[:6]}"


@dataclass
class AppRecord:
    id: str
    name: str
    prompt: str = ""
    owner_id: str = ""
    status: str = "created"  # created|building|deploying|running|error|stopped
    stage: str = "queued"
    mode: str = "unknown"  # static|node|python|unknown
    entry: str = "index.html"
    internal_port: int | None = None
    pid: int | None = None
    preview_path: str = ""
    public_url: str = ""
    conversation_id: str | None = None
    last_error: str = ""
    build_log: str = ""
    files: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def workspace(self) -> Path:
        return config.APPS_DIR / self.id / "project"

    @property
    def base_dir(self) -> Path:
        return config.APPS_DIR / self.id

    @property
    def log_path(self) -> Path:
        return self.base_dir / "logs" / "run.log"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["workspace"] = str(self.workspace)
        return data


class Store:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._apps: dict[str, AppRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for item in raw.get("apps", []):
            known = {f for f in AppRecord.__dataclass_fields__}
            self._apps[item["id"]] = AppRecord(**{k: v for k, v in item.items() if k in known})

    def _save(self) -> None:
        payload = {"apps": [asdict(a) for a in self._apps.values()]}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._path)

    def list(self) -> list[AppRecord]:
        with self._lock:
            return sorted(self._apps.values(), key=lambda a: a.created_at, reverse=True)

    def list_for_owner(self, owner_id: str) -> list[AppRecord]:
        # Legacy apps created before sign-in existed have no owner; keep them
        # visible to every signed-in user so nothing disappears on upgrade.
        with self._lock:
            return [a for a in self.list() if a.owner_id in (owner_id, "")]

    def get(self, app_id: str) -> AppRecord | None:
        with self._lock:
            return self._apps.get(app_id)

    def create(self, name: str, prompt: str, owner_id: str = "") -> AppRecord:
        app_id = new_app_id(name)
        record = AppRecord(id=app_id, name=name or "Untitled app", prompt=prompt, owner_id=owner_id)
        (record.workspace).mkdir(parents=True, exist_ok=True)
        (record.base_dir / "logs").mkdir(parents=True, exist_ok=True)
        record.preview_path = f"/p/{app_id}/"
        with self._lock:
            self._apps[app_id] = record
            self._save()
        return record

    def update(self, app_id: str, **fields: Any) -> AppRecord | None:
        with self._lock:
            record = self._apps.get(app_id)
            if record is None:
                return None
            for key, value in fields.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            record.updated_at = time.time()
            self._save()
            return record

    def delete(self, app_id: str) -> bool:
        with self._lock:
            removed = self._apps.pop(app_id, None) is not None
            if removed:
                self._save()
            return removed


store = Store(config.DATA_DIR / "apps.json")
