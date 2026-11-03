"""Thin client for the local OpenHands agent server.

The runtime exposes an OpenHands agent server on ``127.0.0.1:60000``. We drive
software-generation by creating an agent conversation whose workspace is the
app's project directory, then tail its event stream for live progress.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from . import config


class AgentServerError(RuntimeError):
    pass


@dataclass
class AgentEvent:
    """Normalised view of one agent-server event."""

    kind: str
    source: str = ""
    tool: str = ""
    title: str = ""
    detail: str = ""
    raw: dict[str, Any] | None = None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return ""


def _truncate(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


class AgentClient:
    def __init__(self) -> None:
        self._client = httpx.Client(base_url=config.AGENT_SERVER_URL, timeout=120.0)

    def _headers(self) -> dict[str, str]:
        key = config.SESSION_API_KEY or config._discover_session_api_key()
        if not key:
            raise AgentServerError(
                "Could not find the agent-server session key. Set UGA_AGENT_SESSION_KEY."
            )
        return {"X-Session-API-Key": key, "Content-Type": "application/json"}

    def healthy(self) -> bool:
        try:
            return self._client.get("/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------ #
    # LLM credentials (per-user tokens)
    # ------------------------------------------------------------------ #
    def validate_llm(self, llm: dict[str, Any], *, profile: str = "uga-preflight") -> tuple[bool, str]:
        """Pre-flight an LLM config. Returns (valid, error_message)."""
        try:
            resp = self._client.post(
                f"/api/profiles/{profile}/validate", json={"llm": llm}, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            return False, f"could not reach the agent server: {exc}"
        if resp.status_code != 200:
            return False, f"validation failed: {resp.status_code} {resp.text[:200]}"
        payload = resp.json()
        if payload.get("valid"):
            return True, ""
        error = payload.get("error") or {}
        return False, error.get("message") or "the provider rejected this credential"

    def create_provider_connection(
        self, *, display_name: str, provider: str, api_key: str, base_url: str | None = None
    ) -> str:
        body: dict[str, Any] = {
            "display_name": display_name,
            "provider": provider or "custom",
            "api_key": api_key,
        }
        if base_url:
            body["base_url"] = base_url
        resp = self._client.post(
            "/api/llm/provider-connections", json=body, headers=self._headers()
        )
        if resp.status_code not in (200, 201):
            raise AgentServerError(
                f"create_provider_connection failed: {resp.status_code} {resp.text[:300]}"
            )
        return resp.json()["id"]

    def delete_provider_connection(self, connection_id: str) -> None:
        try:
            self._client.delete(
                f"/api/llm/provider-connections/{connection_id}", headers=self._headers()
            )
        except httpx.HTTPError:
            pass

    def list_providers(self) -> list[str]:
        resp = self._client.get("/api/llm/providers", headers=self._headers())
        if resp.status_code != 200:
            return []
        return resp.json().get("providers", [])

    # ---- subscription (device-code) sign-in --------------------------------
    def subscription_status(self) -> dict[str, Any]:
        resp = self._client.get(
            "/api/llm/subscription/openai/status", headers=self._headers()
        )
        return resp.json() if resp.status_code == 200 else {"connected": False}

    def subscription_device_start(self) -> dict[str, Any]:
        resp = self._client.post(
            "/api/llm/subscription/openai/device/start", headers=self._headers()
        )
        if resp.status_code != 200:
            raise AgentServerError(
                f"device login unavailable: {resp.status_code} {resp.text[:200]}"
            )
        return resp.json()

    def subscription_device_poll(self, device_code: str) -> dict[str, Any]:
        resp = self._client.post(
            "/api/llm/subscription/openai/device/poll",
            json={"device_code": device_code},
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return {"connected": False, "pending": True}
        return resp.json()

    def subscription_logout(self) -> None:
        try:
            self._client.post("/api/llm/subscription/openai/logout", headers=self._headers())
        except httpx.HTTPError:
            pass

    def start_conversation(
        self,
        workspace: str,
        message: str,
        *,
        max_iterations: int | None = None,
        llm: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "workspace": {"working_dir": workspace, "kind": "LocalWorkspace"},
            "agent_settings": {"llm": llm or {"model": config.AGENT_MODEL}},
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": message}],
                "run": True,
            },
            "max_iterations": max_iterations or config.AGENT_MAX_ITERATIONS,
            "autotitle": False,
            "confirmation_policy": {"kind": "NeverConfirm"},
        }
        resp = self._client.post("/api/conversations", json=payload, headers=self._headers())
        if resp.status_code not in (200, 201):
            raise AgentServerError(f"start_conversation failed: {resp.status_code} {resp.text[:400]}")
        return resp.json()["id"]

    def send_message(self, conversation_id: str, message: str, *, run: bool = True) -> None:
        payload = {
            "role": "user",
            "content": [{"type": "text", "text": message}],
            "run": run,
        }
        resp = self._client.post(
            f"/api/conversations/{conversation_id}/events", json=payload, headers=self._headers()
        )
        if resp.status_code not in (200, 201):
            raise AgentServerError(f"send_message failed: {resp.status_code} {resp.text[:300]}")

    def status(self, conversation_id: str) -> str:
        resp = self._client.get(
            "/api/conversations", params={"ids": conversation_id}, headers=self._headers()
        )
        if resp.status_code != 200:
            return "unknown"
        items = resp.json()
        return items[0].get("execution_status", "unknown") if items else "unknown"

    def stop(self, conversation_id: str) -> None:
        self._client.post(f"/api/conversations/{conversation_id}/interrupt", headers=self._headers())

    def events(self, conversation_id: str, *, max_events: int = 4000) -> list[dict[str, Any]]:
        """Return all events for a conversation, following the search pagination."""
        collected: list[dict[str, Any]] = []
        page_id: str | None = None
        while len(collected) < max_events:
            params: dict[str, Any] = {"limit": 100}
            if page_id:
                params["page_id"] = page_id
            resp = self._client.get(
                f"/api/conversations/{conversation_id}/events/search",
                params=params,
                headers=self._headers(),
            )
            if resp.status_code != 200:
                break
            payload = resp.json()
            items = payload.get("items", [])
            collected.extend(items)
            page_id = payload.get("next_page_id")
            if not page_id or not items:
                break
        return collected


def normalise(event: dict[str, Any]) -> AgentEvent | None:
    """Map a raw agent-server event onto something the UI can render."""
    kind = event.get("kind", "")
    source = event.get("source", "")
    if kind == "ActionEvent":
        tool = event.get("tool_name", "")
        action = event.get("action") or {}
        if tool == "terminal":
            detail = action.get("command", "")
        elif tool == "file_editor":
            detail = f"{action.get('command', '')} {action.get('path', '')}".strip()
        elif tool == "finish":
            detail = _text_of(action.get("message"))
        else:
            detail = json.dumps(action)[:2000]
        return AgentEvent(
            kind="action",
            source=source,
            tool=tool,
            title=event.get("summary") or f"{tool} action",
            detail=_truncate(detail),
            raw=event,
        )
    if kind == "ObservationEvent":
        tool = event.get("tool_name", "")
        observation = event.get("observation") or {}
        return AgentEvent(
            kind="observation",
            source=source,
            tool=tool,
            title=f"{tool} result" + (" (error)" if observation.get("is_error") else ""),
            detail=_truncate(_text_of(observation.get("content"))),
            raw=event,
        )
    if kind == "MessageEvent":
        return AgentEvent(
            kind="message",
            source=source,
            title="user message" if source == "user" else "agent message",
            detail=_truncate(_text_of(event.get("content"))),
            raw=event,
        )
    if kind == "ConversationStateUpdateEvent" and event.get("key") == "execution_status":
        return AgentEvent(kind="status", title=str(event.get("value")), raw=event)
    return None
