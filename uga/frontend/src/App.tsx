import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, subscribeEvents } from "./api";
import type { AppRecord, PipelineEvent } from "./types";
import LogStream from "./components/LogStream";
import StatusPill from "./components/StatusPill";

const EXAMPLES = [
  "A pomodoro timer with task labels and session stats",
  "A kanban board with drag-and-drop and localStorage",
  "A tip calculator with a bill split and a live receipt preview",
  "A markdown notes app with a live preview pane",
  "A budget tracker with charts and monthly totals",
  "A landing page for a coffee shop with a menu and booking form",
];

/** The preview server lives on the sibling "work-2" host of the console host. */
function previewBase(): string {
  const { protocol, host } = window.location;
  const swapped = host.replace(/(^|\.)work-1([.-])/, (_m, lead, sep) => `${lead}work-2${sep}`);
  if (swapped !== host) return `${protocol}//${swapped}`;
  return "";
}

function previewUrl(app: AppRecord): string {
  const base = previewBase();
  if (base) return `${base}${app.preview_path}`;
  return app.public_url || app.preview_path;
}

export default function App() {
  const [apps, setApps] = useState<AppRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [draft, setDraft] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [previewNonce, setPreviewNonce] = useState(0);
  const unsubscribe = useRef<null | (() => void)>(null);

  const active = useMemo(() => apps.find((a) => a.id === selected) ?? null, [apps, selected]);

  const refresh = useCallback(async () => {
    try {
      setApps(await api.listApps());
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    setEvents([]);
    unsubscribe.current?.();
    unsubscribe.current = subscribeEvents(selected, (event) => {
      setEvents((prev) => [...prev, event]);
      if (event.kind === "done" || event.kind === "success") {
        refresh();
        setPreviewNonce((n) => n + 1);
      }
      if (event.kind === "stage") refresh();
    }, refresh);
    return () => unsubscribe.current?.();
  }, [selected, refresh]);

  const submit = async () => {
    const prompt = draft.trim();
    if (!prompt) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createApp(prompt);
      setDraft("");
      setApps((prev) => [created, ...prev]);
      setSelected(created.id);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const sendFollowUp = async () => {
    if (!active || !followUp.trim()) return;
    setBusy(true);
    try {
      await api.prompt(active.id, followUp.trim());
      setFollowUp("");
      refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<AppRecord>) => {
    setBusy(true);
    setError("");
    try {
      const updated = await fn();
      setApps((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      setPreviewNonce((n) => n + 1);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (app: AppRecord) => {
    if (!confirm(`Delete "${app.name}" and its files?`)) return;
    await api.remove(app.id).catch((err) => setError(String(err)));
    if (selected === app.id) setSelected(null);
    refresh();
  };

  const working = active ? ["building", "deploying"].includes(active.status) : false;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">U</div>
          <div>
            <h1>UGA</h1>
            <p>describe it · we build &amp; run it</p>
          </div>
        </div>
        <div className="sidebar-head">
          <span>Apps</span>
          <span>{apps.length}</span>
        </div>
        <div className="app-list">
          {apps.length === 0 && <div className="empty">No apps yet.<br />Describe one to get started.</div>}
          {apps.map((app) => (
            <button
              key={app.id}
              className={`app-item ${app.id === selected ? "active" : ""}`}
              onClick={() => setSelected(app.id)}
            >
              <div className="row">
                <span className="name">{app.name}</span>
                <StatusPill status={app.status} stage={app.stage} />
              </div>
              <div className="meta">{app.stage || app.prompt.slice(0, 60)}</div>
            </button>
          ))}
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <span className="title">{active ? active.name : "UGA build platform"}</span>
          {active && <StatusPill status={active.status} stage={active.stage} />}
          <span className="spacer" />
          {active && (
            <div className="row-actions">
              <button className="btn" disabled={busy} onClick={() => act(() => api.rebuild(active.id))}>
                Rebuild
              </button>
              {active.status === "running" ? (
                <button className="btn" disabled={busy} onClick={() => act(() => api.stop(active.id))}>
                  Stop
                </button>
              ) : (
                <button className="btn" disabled={busy} onClick={() => act(() => api.start(active.id))}>
                  Start
                </button>
              )}
              <button className="btn danger" disabled={busy} onClick={() => remove(active)}>
                Delete
              </button>
            </div>
          )}
        </div>

        {error && (
          <div style={{ padding: "12px 26px 0" }}>
            <div className="banner error">{error}</div>
          </div>
        )}

        {!active ? (
          <div className="hero">
            <h1>
              Describe an app. <span>UGA builds it and runs it.</span>
            </h1>
            <p>
              Type what you want in plain English. An AI agent writes the code, the platform
              compiles it, and it goes live on a localhost preview you can open right away.
            </p>
            <div className="card">
              <textarea
                rows={4}
                placeholder="e.g. A weather dashboard for three cities with a 5-day forecast and a dark theme"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
                }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12 }}>
                <span style={{ color: "var(--muted)", fontSize: 12.5 }}>
                  ⌘/Ctrl + Enter to build
                </span>
                <button className="btn primary" disabled={busy || !draft.trim()} onClick={submit}>
                  {busy ? <span className="spin" /> : "Build app"}
                </button>
              </div>
              <div className="chips">
                {EXAMPLES.map((example) => (
                  <button key={example} className="chip" onClick={() => setDraft(example)}>
                    {example}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="content">
            <div className="col">
              <div className="card">
                <h2>What to build</h2>
                <textarea
                  rows={3}
                  value={followUp}
                  placeholder="Ask for a change, e.g. 'add dark mode' or 'add a search bar'"
                  onChange={(e) => setFollowUp(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) sendFollowUp();
                  }}
                />
                <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
                  <button
                    className="btn primary"
                    disabled={busy || !followUp.trim()}
                    onClick={sendFollowUp}
                  >
                    {working ? "Agent is working…" : "Send to agent"}
                  </button>
                </div>
              </div>

              <div className="card" style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 260 }}>
                <h2>Build activity</h2>
                <LogStream events={events} />
              </div>
            </div>

            <div className="col">
              <div className="card" style={{ display: "flex", flexDirection: "column", flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                  <h2 style={{ margin: 0 }}>Live preview</h2>
                  <span className="spacer" style={{ flex: 1 }} />
                  {active.status === "running" && (
                    <>
                      <button
                        className="btn ghost"
                        onClick={() => setPreviewNonce((n) => n + 1)}
                        title="Reload preview"
                      >
                        Reload
                      </button>
                      <a
                        className="btn"
                        href={previewUrl(active)}
                        target="_blank"
                        rel="noreferrer"
                        style={{ textDecoration: "none" }}
                      >
                        Open ↗
                      </a>
                    </>
                  )}
                </div>
                {active.status === "running" ? (
                  <iframe
                    key={`${active.id}-${previewNonce}`}
                    className="preview-frame"
                    src={previewUrl(active)}
                    title="app preview"
                  />
                ) : (
                  <div className="empty">
                    {working
                      ? "Building… the preview appears when the app is running."
                      : "App is not running. Press Start to launch it."}
                  </div>
                )}
              </div>

              <div className="card">
                <h2>Details</h2>
                <div className="kv">
                  <span className="k">status</span>
                  <span className="v">{active.status}</span>
                  <span className="k">stage</span>
                  <span className="v">{active.stage}</span>
                  <span className="k">mode</span>
                  <span className="v">{active.mode}</span>
                  <span className="k">entry</span>
                  <span className="v">{active.entry || "—"}</span>
                  <span className="k">port</span>
                  <span className="v">{active.internal_port ?? "static"}</span>
                  <span className="k">workspace</span>
                  <span className="v">{active.workspace}</span>
                </div>
                {active.last_error && (
                  <div className="banner error" style={{ marginTop: 12 }}>
                    {active.last_error}
                  </div>
                )}
                {active.files.length > 0 && (
                  <>
                    <h2 style={{ marginTop: 16 }}>Files</h2>
                    <div className="files">
                      {active.files.map((file) => (
                        <span className="file" key={file}>
                          {file}
                        </span>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
