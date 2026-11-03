# UGA — build platform

Describe a web app in plain English. An AI agent writes the code, the platform
compiles it, and it goes live on a localhost preview you can open immediately.

```
 describe ──▶ AI agent writes code ──▶ compile / install ──▶ run ──▶ live preview
```

## What it does

- **Sign in with your own token.** UGA runs the agent on *your* LLM credentials,
  so builds are billed to your key, not the platform's. Three sign-in methods:
  - **API key** — paste a provider key (e.g. `openai/gpt-4o-mini`). It is
    validated against the agent server and stored there as a *provider
    connection*; UGA only keeps the connection id and a short fingerprint.
  - **Sign in with OpenAI** — OAuth-style device-code login to your OpenAI
    account (no key to copy).
  - **Demo** — explore on the shared platform token (disable with
    `UGA_ALLOW_FREE_LOGIN=0`).

  Every sign-in defaults to the platform model
  (`openhands/deepseek-v4.1-flash`). Set `UGA_FORCED_MODEL` to pin *all*
  accounts — including bring-your-own-key and OpenAI sign-ins — onto a single
  model, regardless of what they request.
- **Describe it.** Type what you want in the console (e.g. "a kanban board with
  drag-and-drop and localStorage").
- **The agent builds it.** A local OpenHands agent is given the app's own
  workspace directory and writes a complete, runnable project into it.
- **The platform compiles it.** UGA detects the project type and prepares it:
  - plain HTML/CSS/JS — served as-is,
  - Node/Vite/React — `npm install` + `npm run build`, output served statically
    (or its own server started on an allocated port),
  - Python — virtualenv + `pip install -r requirements.txt`, then its server.
- **It runs on localhost.** Each app gets its own port and a stable preview URL.
- **Iterate.** Send follow-up prompts ("add dark mode") to the same agent and the
  app is rebuilt and restarted.

## Architecture

| Piece | Port | Role |
| --- | --- | --- |
| UGA console (backend + UI) | `12000` | REST API, SSE progress stream, serves the React UI |
| UGA preview proxy | `12001` | Serves every running app at `/p/<app_id>/` |
| Local agent server | `60000` | OpenHands agent harness used to write the code |
| Generated apps | `12100–12299` | Loopback ports for apps that run their own server |

```
uga/
  run.py                 starts both servers
  backend/
    config.py            ports, paths, agent-server discovery
    store.py             app registry (JSON)
    auth.py              sessions + per-user LLM credentials
    agent.py             client for the local OpenHands agent server
    runner.py            detect → install → build → run
    orchestrator.py      pipeline + SSE event hub
    proxy.py             preview proxy
    main.py              HTTP API + UI host
  frontend/              React + Vite console UI
  apps/<app_id>/         one workspace per app (project/, logs/)
  data/apps.json         app registry
  data/auth.json         users + sessions (gitignored)
```

### How an app is built

1. `POST /api/apps` creates a record with a workspace at `uga/apps/<id>/project`.
2. The orchestrator starts an OpenHands conversation whose workspace is that
   directory and sends a build prompt.
3. Every agent action/observation is streamed to the UI over SSE and appended to
   `uga/apps/<id>/logs/pipeline.log`.
4. When the agent finishes, `runner.build()` prepares the project and
   `runner.start()` launches it (or marks it as static).
5. The app becomes reachable at `https://<preview-host>/p/<app_id>/`.

## Run it

```bash
cd uga
pip install fastapi "uvicorn[standard]" httpx

cd frontend && npm install && npm run build && cd ..

python3 run.py
```

Then open the console host (`work-1` when running in a hosted runtime) and
describe an app.

### Deploy it

A single-container deployment (console + preview + agent server) is ready for
Fly.io — see [DEPLOY.md](../DEPLOY.md). The image is built from the repository
root:

```bash
fly volumes create uga_data --size 10 --region iad
fly secrets set OH_SECRET_KEY="$(openssl rand -hex 32)" \
                UGA_AGENT_SESSION_KEY="$(openssl rand -hex 32)"
fly deploy
```

On a host that exposes only one port, set `UGA_SINGLE_PORT=1` (the Fly config
does this) and previews are served from the console origin at `/p/<app_id>/`.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `UGA_PORT` | `12000` | console port (falls back to `PORT`) |
| `UGA_PREVIEW_PORT` | `12001` | preview proxy port |
| `UGA_SINGLE_PORT` | off | serve previews from the console port at `/p` instead of a second port |
| `UGA_PUBLIC_BASE` | auto | public origin used to build preview URLs |
| `UGA_AGENT_SERVER_URL` | `http://127.0.0.1:60000` | local agent server |
| `UGA_AGENT_SESSION_KEY` | auto-discovered | agent-server API key |
| `UGA_MODEL` | `openhands/deepseek-v4.1-flash` | default model the agent uses |
| `UGA_FORCED_MODEL` | *(empty)* | pin **every** account onto one model; empty honours each user's own model |
| `UGA_FREE_MODEL` | `UGA_MODEL` | model shown/used for free sign-in |
| `UGA_ALLOW_FREE_LOGIN` | `1` | allow free sign-in on the shared platform model |
| `UGA_MAX_ITERATIONS` | `150` | agent step budget per build |
| `UGA_APP_PORT_START/END` | `12100`/`12299` | ports for app servers |
| `UGA_ALLOW_DEMO_LOGIN` | `1` | allow sign-in on the shared platform token |
| `UGA_SESSION_TTL` | 14 days | session lifetime in seconds |
| `UGA_DEFAULT_PROVIDER` | `openai` | provider assumed when it can't be inferred |

## API

All `/api/apps*` routes require a signed-in session (HttpOnly cookie) and are
scoped to the owner. `GET /api/health` and the `/api/auth/*` routes are public.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | platform + agent-server status |
| `GET` | `/api/auth/methods` | available sign-in methods |
| `GET` | `/api/auth/me` | current session / user |
| `POST` | `/api/auth/login/api-key` | sign in with a provider API key |
| `POST` | `/api/auth/login/demo` | sign in on the shared token |
| `POST` | `/api/auth/device/start` | begin OpenAI device-code login |
| `POST` | `/api/auth/device/poll` | poll a device login |
| `POST` | `/api/auth/logout` | end the session |
| `GET` | `/api/apps` | list *your* apps |
| `POST` | `/api/apps` | create an app and start building |
| `GET` | `/api/apps/{id}` | app detail (with logs) |
| `POST` | `/api/apps/{id}/prompt` | iterate on the app with the agent |
| `POST` | `/api/apps/{id}/rebuild` | recompile and restart (no agent) |
| `POST` | `/api/apps/{id}/start` \| `/stop` | control the running app |
| `DELETE` | `/api/apps/{id}` | delete the app and its files |
| `GET` | `/api/apps/{id}/events` | SSE stream of build activity |
| `GET` | `/api/apps/{id}/log` | tail of the build/run logs |
