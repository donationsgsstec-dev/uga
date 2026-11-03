import type { AppRecord, PipelineEvent } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text.slice(0, 300)}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () =>
    fetch("/api/health").then(
      json<{ ok: boolean; agent_server: boolean; urls: Record<string, string> }>,
    ),

  listApps: () =>
    fetch("/api/apps")
      .then(json<{ apps: AppRecord[] }>)
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
