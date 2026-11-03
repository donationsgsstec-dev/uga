import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AuthMethods, User } from "../types";

interface Props {
  methods: AuthMethods | null;
  onSignedIn: (user: User) => void;
}

type Tab = "free" | "api_key" | "device";

export default function Login({ methods, onSignedIn }: Props) {
  const [tab, setTab] = useState<Tab>("free");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // API-key form
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

  // Device flow
  const [device, setDevice] = useState<{
    attempt_id: string;
    user_code: string;
    verification_uri: string;
    verification_uri_complete: string | null;
  } | null>(null);
  const pollTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current) window.clearInterval(pollTimer.current);
    };
  }, []);

  const freeAvailable = methods?.free ?? true;
  const freeModel = methods?.free_model || "openhands/deepseek-v4.1-flash";
  // When the deployment pins a model, every sign-in runs on it.
  const forcedModel = methods?.forced_model || "";

  // Auto-fill the platform model once sign-in methods load, so anyone who
  // signs in with their account uses it unless they deliberately change it.
  useEffect(() => {
    if (!forcedModel && !model && freeModel) setModel(freeModel);
  }, [forcedModel, freeModel, model]);

  const signInApiKey = async () => {
    setBusy(true);
    setError("");
    try {
      const { user } = await api.loginWithApiKey({
        api_key: apiKey.trim(),
        model: forcedModel || model.trim(),
        provider: provider.trim(),
        base_url: baseUrl.trim() || null,
      });
      onSignedIn(user);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const startDevice = async () => {
    setBusy(true);
    setError("");
    try {
      const start = await api.deviceStart(forcedModel || model.trim());
      setDevice({
        attempt_id: start.attempt_id,
        user_code: start.user_code,
        verification_uri: start.verification_uri,
        verification_uri_complete: start.verification_uri_complete,
      });
      const interval = Math.max(3, start.interval_seconds || 5) * 1000;
      pollTimer.current = window.setInterval(async () => {
        try {
          const result = await api.devicePoll(start.attempt_id);
          if (result.status === "connected" && result.user) {
            if (pollTimer.current) window.clearInterval(pollTimer.current);
            onSignedIn(result.user);
          }
        } catch (err) {
          if (pollTimer.current) window.clearInterval(pollTimer.current);
          setError(String(err));
          setBusy(false);
        }
      }, interval);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  };

  const cancelDevice = async () => {
    if (device) await api.deviceCancel(device.attempt_id).catch(() => undefined);
    if (pollTimer.current) window.clearInterval(pollTimer.current);
    setDevice(null);
    setBusy(false);
  };

  const signInFree = async () => {
    setBusy(true);
    setError("");
    try {
      const { user } = await api.loginFree();
      onSignedIn(user);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="brand" style={{ padding: "0 0 6px" }}>
          <div className="logo">U</div>
          <div>
            <h1>UGA</h1>
            <p>describe it · we build &amp; run it</p>
          </div>
        </div>
        <h2>Start building — free</h2>
        <p className="login-sub">
          {forcedModel ? (
            <>
              All builds run on <b>{forcedModel}</b>, free for everyone. Sign in with
              your own token to build without sharing the platform queue.
            </>
          ) : (
            <>
              Builds run on <b>{freeModel}</b>, free for everyone. Bring your own token
              if you want a different model or higher limits.
            </>
          )}
        </p>

        <div className="tabs">
          {freeAvailable && (
            <button
              className={`tab ${tab === "free" ? "active" : ""}`}
              onClick={() => setTab("free")}
            >
              Free
            </button>
          )}
          <button
            className={`tab ${tab === "api_key" ? "active" : ""}`}
            onClick={() => setTab("api_key")}
          >
            API key
          </button>
          <button
            className={`tab ${tab === "device" ? "active" : ""}`}
            onClick={() => setTab("device")}
          >
            Sign in with OpenAI
          </button>
        </div>

        {error && <div className="banner error">{error}</div>}

        {tab === "free" && freeAvailable && (
          <div className="form">
            <p className="login-note">
              No account or key needed. You'll build on the shared free model
              <b> {freeModel}</b>. Use your own token any time for a different model.
            </p>
            <button className="btn primary" disabled={busy} onClick={signInFree}>
              {busy ? <span className="spin" /> : "Continue for free"}
            </button>
          </div>
        )}

        {tab === "api_key" && (
          <div className="form">
            <label>
              API key
              <input
                type="password"
                value={apiKey}
                placeholder="sk-…"
                autoComplete="off"
                onChange={(e) => setApiKey(e.target.value)}
              />
            </label>
            <label>
              Model
              {forcedModel ? (
                <>
                  <input type="text" value={forcedModel} readOnly disabled />
                  <span className="hint">pinned by the platform</span>
                </>
              ) : (
                <input
                  type="text"
                  value={model}
                  placeholder={freeModel}
                  onChange={(e) => setModel(e.target.value)}
                />
              )}
            </label>
            <div className="form-row">
              <label>
                Provider <span className="hint">optional</span>
                <input
                  type="text"
                  value={provider}
                  placeholder="auto-detect"
                  onChange={(e) => setProvider(e.target.value)}
                />
              </label>
              <label>
                Base URL <span className="hint">optional</span>
                <input
                  type="text"
                  value={baseUrl}
                  placeholder="https://…"
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </label>
            </div>
            <button
              className="btn primary"
              disabled={busy || !apiKey.trim() || !model.trim()}
              onClick={signInApiKey}
            >
              {busy ? <span className="spin" /> : "Sign in"}
            </button>
            <p className="login-note">
              Your key is validated and stored by the local OpenHands agent server as a provider
              connection. UGA never writes the raw secret to disk.
            </p>
          </div>
        )}

        {tab === "device" && (
          <div className="form">
            {!device ? (
              <>
                <p className="login-note">
                  Sign in to your OpenAI account with a device code — no key to copy.
                </p>
                <button className="btn primary" disabled={busy} onClick={startDevice}>
                  {busy ? <span className="spin" /> : "Start sign-in"}
                </button>
              </>
            ) : (
              <div className="device-box">
                <p className="login-note">
                  Open the link below and enter this code to authorise UGA:
                </p>
                <div className="user-code">{device.user_code}</div>
                <a
                  className="btn"
                  href={device.verification_uri_complete || device.verification_uri}
                  target="_blank"
                  rel="noreferrer"
                  style={{ textDecoration: "none", textAlign: "center" }}
                >
                  Open {device.verification_uri.replace(/^https?:\/\//, "")} ↗
                </a>
                <div className="waiting">
                  <span className="spin" /> waiting for you to approve…
                </div>
                <button className="btn ghost" onClick={cancelDevice}>
                  Cancel
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
