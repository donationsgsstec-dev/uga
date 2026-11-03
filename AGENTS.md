# AGENTS.md

Persistent context for this repository. Read this before making changes.

## What this repo is

**UGA build platform** — a web platform where a user describes an app in plain
English, a local AI agent writes the code, and the platform compiles and runs the
result on localhost.

Everything lives in `uga/`. See `uga/README.md` for architecture and the API.

## Runtime facts (important)

This workspace runs inside a hosted OpenHands runtime. Key details that are easy
to get wrong:

- **Local agent server**: `http://127.0.0.1:60000` (OpenHands agent-server). The
  UGA backend drives it to generate app code.
  - `GET /health` needs no auth.
  - `/api/*` requires an `X-Session-API-Key` header. That key is **not** in our
    own environment; `backend/config.py` discovers it by reading the
    `openhands-agent-server` process environment from `/proc`.
  - Create a conversation with `POST /api/conversations`, passing
    `workspace.working_dir`, `agent_settings.llm.model`, and
    `initial_message` with `run: true`.
  - `GET /api/conversations?ids=<id>` returns `execution_status`
    (`running` | `finished` | `error` | `stopped`).
  - `GET /api/conversations/<id>/events/search` **caps `limit` at 100** and
    paginates with `next_page_id`. Do not request more than 100 per call.
- **Model**: `openhands/deepseek-v4.1-flash` (provider profile `Default`).
- **Per-user tokens**: the agent server owns LLM credentials, so UGA never has
  to store raw keys:
  - `POST /api/profiles/{name}/validate` with `{llm:{model,api_key,base_url?}}`
    returns `{valid, error}` — a real auth check, usable as a pre-flight.
  - `POST /api/llm/provider-connections` (`display_name, provider, api_key,
    base_url?`) stores a key server-side and returns an `id`; conversations can
    then use `agent_settings.llm.provider_connection_id` instead of an inline
    key. `GET`/`DELETE /api/llm/provider-connections[/{id}]` manage them.
  - OAuth-style sign-in is the **OpenAI subscription device flow**
    (`/api/llm/subscription/openai/device/{start,poll}`, `.../status`,
    `.../logout`). There is no all-hands.dev/openhands.com OAuth in this
    runtime, so "log in with OpenHands" == this device flow or a BYO API key.
  - `POST /api/conversations` accepts `agent_settings.llm` with either
    `provider_connection_id` (api-key users) or
    `{auth_type:"subscription", subscription_vendor:"openai"}` (device users).
- **Public hosts**: `work-1` serves the UGA console, `work-2` serves app
  previews. Both are the same container on ports `12000` and `12001`.
  The host pattern is `work-1-<runtime-id>.prod-runtime.all-hands.dev`
  (dash separated, not dot).
- **App server ports**: generated apps that run their own server get a loopback
  port from `12100-12299`, reverse-proxied by the preview server.
- Docker, node 24, npm 11, python 3.13 and uv are available.
- Python deps (fastapi, uvicorn, httpx) install into
  `/home/openhands/.local/`; `python3` finds them but the `uvicorn` binary is not
  on `PATH` — start the backend with `python3 run.py`, not the `uvicorn` command.

## Layout

```
uga/
  run.py                 starts the console (12000) and preview (12001) servers
  backend/
    config.py            ports, paths, agent-server key discovery
    store.py             app registry (data/apps.json)
    agent.py             agent-server client + event normalisation
    runner.py            detect mode -> install/build -> start/stop process
    orchestrator.py      build pipeline + SSE EventHub
    proxy.py             preview proxy (/p/<app_id>/)
    main.py              HTTP API + UI host
  frontend/              React + Vite console (build with `npm run build`)
  apps/<app_id>/         per-app workspace (project/, logs/) - gitignored
  data/apps.json         registry - gitignored
```

## Working conventions

- Run the platform with `cd uga && python3 run.py`. Logs go to `/tmp/uga.log`.
- Build the UI after editing `frontend/src`: `cd uga/frontend && npm run build`.
  The backend serves `frontend/dist`, so a rebuild is required to see changes.
- A full agent build takes **2-7 minutes** (the model is not fast). Poll
  `GET /api/apps/<id>` or watch the SSE stream; do not assume failure early.
- `runner._static_root` prefers build output dirs (`dist`, `build`, `out`) over
  the project root, so compiled output is served instead of unbuilt source.
- Kill the backend by PID: `pgrep -f "uga/run.py"` then `kill <pid>`. Avoid
  broad `pkill -f python`.
- `backend/store.py` persists on every mutation, including delete.
- Keep the pipeline observable: every stage should `log(app_id, ...)` so the UI
  and `logs/pipeline.log` stay in sync.

## Auth (sign-in with the user's own token)

- `backend/auth.py` holds sessions + users, persisted to `data/auth.json`
  (gitignored). Sessions are opaque ids in the `uga_session` HttpOnly cookie.
- Every `/api/apps*` route depends on `main.current_user` and is owner-scoped.
  Apps with an empty `owner_id` predate sign-in and stay visible to everyone.
- Sign-in methods: `api-key` (BYO key → provider connection on the agent
  server), `device` (OpenAI device flow), `demo` (platform token; toggle with
  `UGA_ALLOW_DEMO_LOGIN=0`).
- The build uses the owner's token: `orchestrator._run_build` resolves
  `auth_store.get_user(record.owner_id).llm_settings()` and passes it as
  `agent_settings.llm`. Demo owners get `None` → platform default profile.
- **Never** persist a raw API key in UGA; store the agent-server
  `provider_connection_id` plus a fingerprint only.

## Testing checklist

After backend changes, verify all three build modes still work:

1. **static** — prompt for a plain HTML/CSS/JS app; expect `mode=static`,
   `internal_port=None`, and a 200 on `/p/<id>/`.
2. **node** — prompt for a React + Vite app; expect `npm run build` to run and
   `dist/index.html` to be served.
3. **python** — prompt for a Flask app reading `PORT`; expect a loopback port in
   `12100-12299` and a 200 through the preview proxy.

Also exercise: follow-up `POST /api/apps/<id>/prompt`, `rebuild`, `start`,
`stop`, and `DELETE`.

Auth checks: `/api/apps*` must return 401 without a session; sign in with
`POST /api/auth/login/demo`, then confirm `GET /api/apps` lists the legacy
ownerless app; `POST /api/auth/logout` must make it 401 again.
