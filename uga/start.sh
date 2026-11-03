#!/usr/bin/env bash
# Container entrypoint for single-container deployments (Fly.io, Railway, VPS).
#
# Starts the OpenHands agent server on loopback, waits for it to become ready,
# then starts the UGA console + preview on the single public port. The agent
# server is never exposed publicly; UGA talks to it over 127.0.0.1.
set -euo pipefail

cd "$(dirname "$0")"

AGENT_HOST="${UGA_AGENT_HOST:-127.0.0.1}"
AGENT_PORT="${UGA_AGENT_PORT:-60000}"
AGENT_SERVER_URL="${UGA_AGENT_SERVER_URL:-http://${AGENT_HOST}:${AGENT_PORT}}"
export UGA_AGENT_SERVER_URL="${AGENT_SERVER_URL}"

# ---- agent server credentials ------------------------------------------------
# A stable OH_SECRET_KEY is required: it encrypts per-conversation LLM keys and
# secrets, so if it changes across restarts those stored values are unreadable.
if [[ -z "${OH_SECRET_KEY:-}" ]]; then
  echo "[start] WARNING: OH_SECRET_KEY is unset; generating an ephemeral one." >&2
  echo "[start] Set it as a secret so encrypted credentials survive restarts." >&2
  export OH_SECRET_KEY="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi

# The agent server authenticates /api/* with X-Session-API-Key. UGA reads the
# key from UGA_AGENT_SESSION_KEY (falling back to SESSION_API_KEY).
if [[ -z "${OH_SESSION_API_KEYS_0:-}" && -z "${UGA_AGENT_SESSION_KEY:-}" && -z "${SESSION_API_KEY:-}" ]]; then
  export OH_SESSION_API_KEYS_0="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
if [[ -z "${UGA_AGENT_SESSION_KEY:-}" ]]; then
  export UGA_AGENT_SESSION_KEY="${OH_SESSION_API_KEYS_0:-${SESSION_API_KEY:-}}"
fi

echo "[start] agent server -> ${AGENT_SERVER_URL}"
python -m openhands.agent_server --host "${AGENT_HOST}" --port "${AGENT_PORT}" &
AGENT_PID=$!

# ---- graceful shutdown -------------------------------------------------------
shutdown() {
  echo "[start] shutting down"
  kill "${AGENT_PID}" 2>/dev/null || true
  [[ -n "${UGA_PID:-}" ]] && kill "${UGA_PID}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap shutdown SIGTERM SIGINT

# ---- wait for readiness ------------------------------------------------------
echo "[start] waiting for agent server to become ready..."
for _ in $(seq 1 120); do
  if ! kill -0 "${AGENT_PID}" 2>/dev/null; then
    echo "[start] agent server exited during startup" >&2
    exit 1
  fi
  if python - <<'PY' 2>/dev/null; then
import os, sys, urllib.request
url = os.environ["UGA_AGENT_SERVER_URL"].rstrip("/") + "/health"
try:
    with urllib.request.urlopen(url, timeout=2) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
    echo "[start] agent server ready"
    break
  fi
  sleep 2
done

# ---- console + preview -------------------------------------------------------
echo "[start] starting UGA on port ${PORT:-${UGA_PORT:-12000}}"
python run.py &
UGA_PID=$!

wait -n "${AGENT_PID}" "${UGA_PID}"
shutdown
