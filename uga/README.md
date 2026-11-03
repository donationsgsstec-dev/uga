# UGA — build platform

Describe a web app in plain English. An AI agent writes the code, the platform
compiles it, and it goes live on a localhost preview you can open immediately.

```
 describe ──▶ AI agent writes code ──▶ compile / install ──▶ run ──▶ live preview
```

## What it does

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
    agent.py             client for the local OpenHands agent server
    runner.py            detect → install → build → run
    orchestrator.py      pipeline + SSE event hub
    proxy.py             preview proxy
    main.py              HTTP API + UI host
  frontend/              React + Vite console UI
  apps/<app_id>/         one workspace per app (project/, logs/)
  data/apps.json         app registry
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

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `UGA_PORT` | `12000` | console port |
| `UGA_PREVIEW_PORT` | `12001` | preview proxy port |
| `UGA_AGENT_SERVER_URL` | `http://127.0.0.1:60000` | local agent server |
| `UGA_AGENT_SESSION_KEY` | auto-discovered | agent-server API key |
| `UGA_MODEL` | `openhands/deepseek-v4.1-flash` | model the agent uses |
| `UGA_MAX_ITERATIONS` | `150` | agent step budget per build |
| `UGA_APP_PORT_START/END` | `12100`/`12299` | ports for app servers |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | platform + agent-server status |
| `GET` | `/api/apps` | list apps |
| `POST` | `/api/apps` | create an app and start building |
| `GET` | `/api/apps/{id}` | app detail (with logs) |
| `POST` | `/api/apps/{id}/prompt` | iterate on the app with the agent |
| `POST` | `/api/apps/{id}/rebuild` | recompile and restart (no agent) |
| `POST` | `/api/apps/{id}/start` \| `/stop` | control the running app |
| `DELETE` | `/api/apps/{id}` | delete the app and its files |
| `GET` | `/api/apps/{id}/events` | SSE stream of build activity |
| `GET` | `/api/apps/{id}/log` | tail of the build/run logs |
