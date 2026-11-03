# UGA — describe an app, get a running app

**UGA** is a self-hosted build platform. You describe a web app in plain
English; an AI agent writes the code, the platform compiles it, and it goes live
on a preview URL you can open immediately.

```
 describe ──▶ AI agent writes code ──▶ compile / install ──▶ run ──▶ live preview
```

It runs as a single container — console, preview proxy, and agent server — and
is set up to deploy on [Fly.io](DEPLOY.md).

## Quick start

```bash
cd uga
pip install -r requirements.txt

cd frontend && npm install && npm run build && cd ..

python3 run.py
```

Open the console at `http://localhost:12000/` and describe an app, e.g.
_"a kanban board with drag-and-drop and localStorage"_.

### Requirements

- Python 3.12+
- Node.js + npm (to build the console UI, and to build/run generated Node apps)
- An OpenHands agent server on `127.0.0.1:60000` — see below

### The agent server

UGA drives a local OpenHands agent server to write code. It must be running and
reachable at `UGA_AGENT_SERVER_URL` (default `http://127.0.0.1:60000`):

```bash
pip install openhands-agent-server==1.59.0
export OH_SECRET_KEY="$(openssl rand -hex 32)"        # keep stable; encrypts stored credentials
export UGA_AGENT_SESSION_KEY="$(openssl rand -hex 32)" # UGA <-> agent server auth
python -m openhands.agent_server --host 127.0.0.1 --port 60000
```

`uga/start.sh` does all of this for you in a container (starts the agent server,
waits for it to be ready, then launches UGA).

> **LLM credentials:** the agent server makes the model calls, so it needs a
> reachable LLM endpoint. Users who sign in with their own API key bring their
> own; free/demo sign-ins need a platform credential (`LLM_MODEL` +
> `LLM_API_KEY`). See [DEPLOY.md](DEPLOY.md#llm-credentials--read-this-before-deploying).

## Sign-in and models

Users sign in one of three ways, and UGA runs the agent on **their** credentials
so builds are billed to their key:

- **API key** — validated against the agent server and stored there as a
  *provider connection*; UGA keeps only the connection id and a fingerprint.
- **Sign in with OpenAI** — OAuth-style device-code login.
- **Demo** — the shared platform token (disable with `UGA_ALLOW_FREE_LOGIN=0`).

Every sign-in defaults to `openhands/deepseek-v4.1-flash`. Set
`UGA_FORCED_MODEL` to pin **all** accounts onto a single model regardless of what
they request.

## What the platform supports

It detects the project type and prepares it automatically:

| Detected | How it is prepared |
| --- | --- |
| Plain HTML/CSS/JS | served as-is |
| Node / Vite / React | `npm install` + `npm run build`; output served statically, or its own server started on an allocated port |
| Python | virtualenv + `pip install -r requirements.txt`, then its server |

Follow-up prompts ("add dark mode") iterate on the same app and rebuild it.

## Deploy

Single-container deployment on Fly.io — one public port, always on, with a
persistent volume for state:

```bash
fly volumes create uga_data --size 10 --region iad
fly secrets set OH_SECRET_KEY="$(openssl rand -hex 32)" \
                UGA_AGENT_SESSION_KEY="$(openssl rand -hex 32)"
fly deploy
```

Full walkthrough, costs, and limitations: **[DEPLOY.md](DEPLOY.md)**.

## Repository layout

```
Dockerfile               single-container image (build context = repo root)
fly.toml                 Fly.io app: one port, always-on, /data volume
DEPLOY.md                deployment guide
AGENTS.md                notes for AI agents working in this repo
uga/
  run.py                 starts the console (12000) and preview (12001) servers
  start.sh               container entrypoint: agent server + UGA
  backend/
    config.py            ports, paths, agent-server key discovery, single-port mode
    store.py             app registry (JSON)
    auth.py              sessions + per-user LLM credentials
    agent.py             client for the local OpenHands agent server
    runner.py            detect -> install -> build -> run
    orchestrator.py      build pipeline + SSE event hub
    proxy.py             preview proxy (/p/<app_id>/)
    main.py              HTTP API + UI host
  frontend/              React + Vite console UI
  apps/<app_id>/         per-app workspace (project/, logs/) - gitignored
  data/                  app registry + users/sessions - gitignored
```

App-level documentation (API reference, environment variables, architecture):
**[uga/README.md](uga/README.md)**.

## Ports

| Piece | Port |
| --- | --- |
| UGA console (API + UI) | `12000` (or `PORT`) |
| UGA preview proxy | `12001` (single-port mode serves previews at `/p` on `12000`) |
| OpenHands agent server | `60000` (loopback only) |
| Generated apps | `12100–12299` (loopback) |

## Notes and limitations

- **Single instance.** The build hub and app registry are in-process; do not
  scale horizontally.
- **No scale-to-zero** in the Fly config — builds run in background threads for
  minutes and would be killed.
- **Generated code is executed on the host.** It is not sandboxed; only expose
  this to users you trust.

## License

No license file is included; treat as all rights reserved unless one is added.
