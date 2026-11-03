# Deploying UGA to Fly.io

Single-container deployment: the UGA console, the preview proxy, and the
OpenHands agent server all run inside one Fly Machine, behind **one** public
port. Persistent state lives on a Fly volume mounted at `/data`.

```
              ┌─────────────── Fly Machine (always on) ───────────────┐
  :443  ───▶  │  UGA console + preview  (uvicorn, 0.0.0.0:12000)      │
              │        │                                              │
              │        ▼  http://127.0.0.1:60000                     │
              │  OpenHands agent server  (loopback only)             │
              │                                                      │
              │  /data  ◀── Fly volume (registry, auth, apps)        │
              └──────────────────────────────────────────────────────┘
```

Why one port: `fly.toml` sets `UGA_SINGLE_PORT=1`, which mounts the preview
proxy into the console app at `/p/<app_id>/` instead of binding a second port.

## Prerequisites

1. A Fly.io account and `flyctl` installed (`curl -L https://fly.io/install.sh | sh`).
2. `fly auth login`.

## 1. Create the app and volume

`fly.toml` declares `app = "uga"`. If that name is taken, change it (and the
`FLY_APP_NAME`-derived URLs follow automatically), or let flyctl pick one:

```bash
cd /path/to/repo          # repository root (contains fly.toml + uga/)
fly launch --no-deploy    # or: fly apps create <unique-name>
fly volumes create uga_data --size 10 --region iad
```

`primary_region` in `fly.toml` must match the volume's region.

## 2. Set secrets

```bash
# Stable key that encrypts per-conversation LLM credentials. KEEP IT STABLE:
# if it changes, previously stored credentials become unreadable.
fly secrets set OH_SECRET_KEY="$(openssl rand -hex 32)"

# Key UGA uses to call the agent server's /api/* (they share the machine).
fly secrets set UGA_AGENT_SESSION_KEY="$(openssl rand -hex 32)"
```

### LLM credentials — read this before deploying

The agent server executes the model calls that actually build the apps, so it
needs a reachable LLM endpoint. **Nothing in this repository supplies one.**

| Sign-in path | What the agent server needs |
|---|---|
| Each user brings their own API key | Nothing extra — UGA stores the key as a per-user *provider connection* on the agent server |
| Free / demo users | A platform-wide credential |

Free and demo sign-ins run on the platform model (`openhands/deepseek-v4.1-flash`
by default) and pass no credential, so they only work if the agent server can
reach a provider. Two options:

- **Simplest:** put a credential on the agent server's default profile
  (`LLM_MODEL` + `LLM_API_KEY`), then redeploy. Verify with `fly ssh console` and
  a build from the console UI.
- **More robust:** deploy with an OpenAI-compatible gateway in front and set
  `base_url` accordingly.

In this OpenHands runtime the credential comes from a managed refresh endpoint
(`OH_LLM_API_KEY_REFRESH_*`) that is specific to that environment; it is **not**
reproducible on Fly. Set an explicit key here.

## 3. Deploy

```bash
fly deploy
```

The image build compiles the frontend and installs `openhands-agent-server`.

## 4. Verify

```bash
fly status
curl -s https://<app>.fly.dev/api/health   # {"ok":true,"agent_server":true,...}
fly logs
```

Then open `https://<app>.fly.dev/` and build an app. Previews appear at
`https://<app>.fly.dev/p/<app_id>/`.

## Configuration reference

| Env var | Default | Purpose |
|---|---|---|
| `PORT` | `12000` | Public port (Fly/PaaS convention; `UGA_PORT` also honoured) |
| `UGA_SINGLE_PORT` | off | Serve previews from the console port at `/p` |
| `UGA_PUBLIC_BASE` | auto | Public origin used for preview URLs |
| `UGA_AGENT_SERVER_URL` | `http://127.0.0.1:60000` | Agent server address |
| `UGA_AGENT_SESSION_KEY` | — | Session key UGA sends to the agent server |
| `UGA_FORCED_MODEL` | `openhands/deepseek-v4.1-flash` | Pin every build to one model |
| `UGA_DATA_DIR` / `UGA_APPS_DIR` | `/data/uga[/apps]` | Persistent state |

## Costs

Always-on `shared-cpu-2x` (2 GB) is roughly **$20–25/month**, plus ~$1.50 for a
10 GB volume. Size down with `[[vm]] size = "shared-cpu-1x"` / `memory = "1gb"`
if the agent builds fit (they are memory-hungry; 512 MB is likely too small).

## Limitations

- **Single instance.** The build hub and app registry are in-process, so the
  app must not be scaled horizontally (`fly scale count 1`).
- **No scale-to-zero.** `auto_stop_machines = "off"` is deliberate: builds run in
  background threads for minutes and would be killed on stop.
- **Code execution.** The platform runs generated code on the machine. It is not
  sandboxed — only expose this to users you trust, and prefer the agent server's
  Docker-sandbox mode for untrusted input.
- **Volume is per-machine.** Recreating the machine in another region requires a
  new volume.

## Updating

```bash
git pull && fly deploy
```

The `/data` volume is untouched by deploys, so users and apps survive.
