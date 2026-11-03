# Single-container image for the UGA build platform.
#
# Build context is the repository root; the application lives under uga/.
# Bundles the Python backend, the OpenHands agent server, and a Node toolchain
# (needed to build and run the apps the platform generates). The frontend is
# built at image build time so no build step is required at runtime.
FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    UGA_HOST=0.0.0.0 \
    UGA_PORT=12000 \
    UGA_AGENT_HOST=127.0.0.1 \
    UGA_AGENT_PORT=60000

# git: agent checkouts; curl/xz: Node download; procps: process tooling the
# platform shells out to. No Node from apt so we control the version.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git curl ca-certificates xz-utils procps \
 && rm -rf /var/lib/apt/lists/*

# Node + npm from the official arch-aware tarball (build and run generated apps).
ARG NODE_VERSION=22.14.0
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) node_arch="x64" ;; \
      arm64) node_arch="arm64" ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" -o /tmp/node.tar.xz; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner; \
    rm /tmp/node.tar.xz; \
    node --version; npm --version

WORKDIR /app

# Python deps first so they cache independently of source changes.
COPY uga/requirements.txt ./requirements.txt
COPY uga/backend/requirements-agent.txt ./requirements-agent.txt
RUN pip install -r requirements.txt -r requirements-agent.txt

# Frontend: install and build the console bundle into uga/frontend/dist.
COPY uga/frontend/package.json uga/frontend/package-lock.json ./uga/frontend/
RUN cd uga/frontend && npm ci
COPY uga/frontend/ ./uga/frontend/
RUN cd uga/frontend && npm run build

# Backend + entrypoint.
COPY uga/backend/ ./uga/backend/
COPY uga/run.py uga/start.sh ./uga/
RUN chmod +x uga/start.sh

# Persistent state (registry, auth, generated apps). The Fly volume is mounted
# over this path by fly.toml; do not declare VOLUME here (Fly manages the mount).
ENV UGA_DATA_DIR=/data/uga \
    UGA_APPS_DIR=/data/uga/apps
RUN mkdir -p /data/uga/apps

EXPOSE 12000

CMD ["./uga/start.sh"]
