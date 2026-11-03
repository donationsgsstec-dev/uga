import type { AppRecord, AuthMethods, DeviceStart, PipelineEvent, User } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    let message = `${res.status}: ${text.slice(0, 300)}`;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") message = parsed.detail;
    } catch {
      /* keep the raw message */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export class UnauthorizedError extends Error {}

export const api = {
  health: () =>
    fetch("/api/health").then(
      json<{ ok: boolean; agent_server: boolean; urls: Record<string, string> }>,
    ),

  // ---- auth ---------------------------------------------------------------
  authMethods: () => fetch("/api/auth/methods").then(json<AuthMethods>),

  me: () =>
    fetch("/api/auth/me").then(
      json<{ authenticated: boolean; user?: User; auto?: boolean }>,
    ),

  loginWithApiKey: (body: {
    api_key: string;
    model: string;
    provider?: string;
    base_url?: string | null;
  }) =>
    fetch("/api/auth/login/api-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{ user: User }>),

  loginDemo: () =>
    fetch("/api/auth/login/demo", { method: "POST" }).then(json<{ user: User }>),

  loginFree: () =>
    fetch("/api/auth/login/free", { method: "POST" }).then(json<{ user: User }>),

  deviceStart: (model: string) =>
    fetch("/api/auth/device/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }).then(json<DeviceStart>),

  devicePoll: (attempt_id: string) =>
    fetch("/api/auth/device/poll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attempt_id }),
    }).then(json<{ status: "pending" | "connected"; user?: User }>),

  deviceCancel: (attempt_id: string) =>
    fetch("/api/auth/device/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attempt_id }),
    }).then(json<unknown>),

  logout: () => fetch("/api/auth/logout", { method: "POST" }).then(json<unknown>),

  // ---- apps ---------------------------------------------------------------
  listApps: () =>
    fetch("/api/apps")
      .then((res) => {
        if (res.status === 401) throw new UnauthorizedError("not signed in");
        return json<{ apps: AppRecord[] }>(res);
      })
      .then((d) => d.apps),

  getApp: (id: string) => fetch(`/api/apps/${id}`).then(json<AppRecord>),

  createApp: (prompt: string, name = "") =>
    fetch("/api/apps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, name }),
    }).then(json<AppRecord>),

  prompt: (id: string, prompt: string) =>
    fetch(`/api/apps/${id}/prompt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    }).then(json<AppRecord>),

  rebuild: (id: string) =>
    fetch(`/api/apps/${id}/rebuild`, { method: "POST" }).then(json<AppRecord>),
  start: (id: string) =>
    fetch(`/api/apps/${id}/start`, { method: "POST" }).then(json<AppRecord>),
  stop: (id: string) =>
    fetch(`/api/apps/${id}/stop`, { method: "POST" }).then(json<AppRecord>),
  remove: (id: string) => fetch(`/api/apps/${id}`, { method: "DELETE" }).then(json<unknown>),
};

export function subscribeEvents(
  id: string,
  onEvent: (event: PipelineEvent) => void,
  onDone: () => void,
): () => void {
  const source = new EventSource(`/api/apps/${id}/events`);
  source.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data) as PipelineEvent;
      if (event.kind !== "ping") onEvent(event);
    } catch {
      /* ignore malformed frames */
    }
  };
  source.onerror = () => {
    source.close();
    onDone();
  };
  return () => source.close();
}
