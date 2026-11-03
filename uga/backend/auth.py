"""Authentication and per-user LLM credentials for the UGA platform.

Sign-in exists so a user can run the agent on their *own* LLM token instead of
the platform's. Two real methods are supported, both backed by the local
OpenHands agent server:

* ``api_key``      — the user pastes their provider API key. We validate it
  against the agent server and stash it there as a *provider connection*, so the
  raw secret never lands on the UGA disk. We only persist the connection id and
  a non-reversible fingerprint.
* ``subscription`` — OAuth-style device-code login to the user's OpenAI account
  (``/api/llm/subscription/openai/device/*``).

The default model (``openhands/deepseek-v4.1-flash``) is free, so a third
``free`` mode reuses the platform's default profile and lets everyone build
without a personal token. It is on by default; set ``UGA_ALLOW_FREE_LOGIN=0`` to
require a personal token instead. All free users share one platform identity.

Sessions are opaque random ids in an HttpOnly cookie, persisted to
``data/auth.json`` (gitignored), so restarts don't sign everyone out.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import config

SESSION_COOKIE = "uga_session"
SESSION_TTL = int(os.environ.get("UGA_SESSION_TTL", str(60 * 60 * 24 * 14)))  # 14 days
# The platform model is free, so everyone can use it without their own token.
ALLOW_FREE = os.environ.get("UGA_ALLOW_FREE_LOGIN", "1") not in ("0", "false", "False")
DEFAULT_PROVIDER = os.environ.get("UGA_DEFAULT_PROVIDER", "openai")
FREE_MODEL = os.environ.get("UGA_FREE_MODEL", config.AGENT_MODEL)

# Token sources that run on the shared platform model.
PLATFORM_SOURCES = ("free", "demo")

# A pinned model forces every account onto one model, whatever they sign in
# with. Empty means each user keeps their own model.
FORCED_MODEL = config.FORCED_MODEL


class AuthError(RuntimeError):
    """Raised for invalid sign-in attempts or missing sessions."""


@dataclass
class User:
    id: str
    token_source: str  # "api_key" | "subscription" | "demo"
    model: str
    provider: str = ""
    base_url: str | None = None
    provider_connection_id: str | None = None
    key_fingerprint: str = ""
    account_email: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def llm_settings(self) -> dict[str, Any] | None:
        """The ``agent_settings.llm`` override for this user's builds.

        ``None`` means "use the platform default profile" — true for free/demo
        users, who run on the shared free model. When a model is pinned
        (``UGA_FORCED_MODEL``), every account is routed onto it here, keeping
        whatever auth the user signed in with.
        """
        if self.token_source in PLATFORM_SOURCES:
            if FORCED_MODEL:
                return {"model": FORCED_MODEL}
            return None
        model = FORCED_MODEL or self.model
        llm: dict[str, Any] = {"model": model}
        if self.token_source == "subscription":
            llm["auth_type"] = "subscription"
            llm["subscription_vendor"] = "openai"
        elif self.provider_connection_id:
            # The agent server resolves the shared secret at use-time.
            llm["provider_connection_id"] = self.provider_connection_id
            if self.base_url:
                llm["base_url"] = self.base_url
        return llm

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "token_source": self.token_source,
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "key_fingerprint": self.key_fingerprint,
            "account_email": self.account_email,
            "created_at": self.created_at,
        }


@dataclass
class Session:
    id: str
    user_id: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + SESSION_TTL)


def fingerprint(secret: str) -> str:
    """Short, non-reversible id for a token — safe to show and store."""
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return f"{digest[:6]}…{digest[-4:]}"


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


class AuthStore:
    def __init__(self, path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._users: dict[str, User] = {}
        self._sessions: dict[str, Session] = {}
        # Pending device-code logins, keyed by a browser-visible attempt id.
        self._attempts: dict[str, dict[str, Any]] = {}
        self._load()

    # ---------------------------------------------------------------- persist
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        user_fields = set(User.__dataclass_fields__)
        session_fields = set(Session.__dataclass_fields__)
        for item in raw.get("users", []):
            self._users[item["id"]] = User(**{k: v for k, v in item.items() if k in user_fields})
        for item in raw.get("sessions", []):
            if item.get("expires_at", 0) > time.time():
                self._sessions[item["id"]] = Session(
                    **{k: v for k, v in item.items() if k in session_fields}
                )

    def _save(self) -> None:
        payload = {
            "users": [asdict(u) for u in self._users.values()],
            "sessions": [asdict(s) for s in self._sessions.values()],
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._path)

    # ------------------------------------------------------------------ users
    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def get_or_create_platform_user(self, *, token_source: str = "free") -> User:
        """A single shared identity for everyone on the free platform model."""
        with self._lock:
            for user in self._users.values():
                if user.token_source == token_source:
                    return user
            user = User(
                id=new_id("usr"),
                token_source=token_source,
                model=config.resolve_model(FREE_MODEL),
                provider="platform",
            )
            self._users[user.id] = user
            self._save()
            return user

    def upsert_user(
        self,
        *,
        user_id: str | None,
        token_source: str,
        model: str,
        provider: str = "",
        base_url: str | None = None,
        provider_connection_id: str | None = None,
        key_fingerprint: str = "",
        account_email: str = "",
    ) -> User:
        with self._lock:
            existing = self._users.get(user_id) if user_id else None
            user = User(
                id=existing.id if existing else new_id("usr"),
                token_source=token_source,
                model=model,
                provider=provider,
                base_url=base_url,
                provider_connection_id=provider_connection_id,
                key_fingerprint=key_fingerprint,
                account_email=account_email,
                created_at=existing.created_at if existing else time.time(),
            )
            self._users[user.id] = user
            self._save()
            return user

    # --------------------------------------------------------------- sessions
    def create_session(self, user_id: str) -> Session:
        with self._lock:
            session = Session(id=new_id("sid"), user_id=user_id)
            self._sessions[session.id] = session
            self._save()
            return session

    def resolve_session(self, session_id: str | None) -> tuple[Session, User] | None:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.expires_at <= time.time():
                self._sessions.pop(session_id, None)
                self._save()
                return None
            user = self._users.get(session.user_id)
            return (session, user) if user else None

    def destroy_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            if self._sessions.pop(session_id, None) is not None:
                self._save()

    # --------------------------------------------------------- device logins
    def start_attempt(self, device_code: str, **meta: Any) -> str:
        attempt_id = new_id("dev")
        with self._lock:
            self._attempts[attempt_id] = {
                "device_code": device_code,
                "created_at": time.time(),
                **meta,
            }
        return attempt_id

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._attempts.get(attempt_id)

    def finish_attempt(self, attempt_id: str) -> None:
        with self._lock:
            self._attempts.pop(attempt_id, None)


auth_store = AuthStore(config.DATA_DIR / "auth.json")
