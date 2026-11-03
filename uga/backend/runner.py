"""Compile and run generated apps on localhost."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from . import config
from .store import AppRecord

BUILD_TIMEOUT = int(os.environ.get("UGA_BUILD_TIMEOUT", "600"))
INSTALL_TIMEOUT = int(os.environ.get("UGA_INSTALL_TIMEOUT", "900"))

# Compiled output directories, checked before the project root so a build
# artefact (e.g. Vite's dist/) is served instead of the unbuilt source.
BUILD_OUTPUT_DIRS = ("dist", "build", "out", "_site")
FALLBACK_DIRS = ("public", "static")
STATIC_ENTRIES = ("index.html", "index.htm", "main.html")
PY_ENTRY_CANDIDATES = ("main.py", "app.py", "server.py", "run.py", "wsgi.py", "asgi.py")
RUN_SCRIPTS = ("run.sh", "start.sh", "serve.sh", "dev.sh")
IGNORED_TOP_LEVEL = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".cache"}


class RunnerError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _log(logfile: Path, text: str) -> None:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open("a") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")


def _run(
    cmd: list[str],
    cwd: Path,
    logfile: Path,
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    _log(logfile, f"\n$ {' '.join(cmd)}")
    merged = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=merged,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "ignore")
        _log(logfile, output + f"\n[timed out after {timeout}s]")
        return 124, output + f"\n[timed out after {timeout}s]"
    except FileNotFoundError as exc:
        _log(logfile, f"[command not found: {exc}]")
        return 127, f"command not found: {exc}"
    _log(logfile, proc.stdout or "")
    return proc.returncode, proc.stdout or ""


def free_port(start: int, end: int) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    raise RunnerError(f"no free port in range {start}-{end}")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def list_files(workspace: Path, limit: int = 200) -> list[str]:
    files: list[str] = []
    for path in sorted(workspace.rglob("*")):
        rel = path.relative_to(workspace)
        if any(part in IGNORED_TOP_LEVEL for part in rel.parts):
            continue
        if path.is_file():
            files.append(str(rel))
        if len(files) >= limit:
            break
    return files


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def detect_mode(workspace: Path) -> str:
    if (workspace / "package.json").exists():
        return "node"
    if (workspace / "requirements.txt").exists() or (workspace / "pyproject.toml").exists():
        return "python"
    if any((workspace / s).exists() for s in RUN_SCRIPTS):
        return "python"
    if any((workspace / s).exists() for s in PY_ENTRY_CANDIDATES):
        return "python"
    return "static"


def _has_index(root: Path) -> bool:
    return root.is_dir() and any((root / name).is_file() for name in STATIC_ENTRIES)


def _static_root(workspace: Path) -> Path | None:
    for candidate in BUILD_OUTPUT_DIRS:
        root = workspace / candidate
        if _has_index(root):
            return root
    if any((workspace / name).is_file() for name in STATIC_ENTRIES):
        return workspace
    for candidate in FALLBACK_DIRS:
        root = workspace / candidate
        if _has_index(root):
            return root
    return None


def _find_entry_html(workspace: Path) -> str:
    root = _static_root(workspace)
    if root is None:
        return ""
    for name in STATIC_ENTRIES:
        if (root / name).is_file():
            rel = (root / name).relative_to(workspace)
            return str(rel)
    return ""


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build(app: AppRecord) -> tuple[bool, str, str]:
    """Compile/prepare the app. Returns (ok, mode, log)."""
    workspace = app.workspace
    logfile = app.base_dir / "logs" / "build.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    logfile.write_text(f"# build for {app.id} @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    mode = detect_mode(workspace)
    _log(logfile, f"detected mode: {mode}")

    if mode == "node":
        ok = _build_node(workspace, logfile)
    elif mode == "python":
        ok = _build_python(workspace, logfile)
    else:
        ok = True
        if _static_root(workspace) is None:
            _log(logfile, "warning: no index.html found; nothing to serve yet")

    return ok, mode, logfile.read_text()


def _npm_install(workspace: Path, logfile: Path) -> bool:
    if (workspace / "node_modules").is_dir():
        _log(logfile, "node_modules present, skipping install")
        return True
    cmd = ["npm", "install", "--no-audit", "--no-fund"]
    if (workspace / "package-lock.json").exists():
        cmd = ["npm", "ci", "--no-audit", "--no-fund"]
    code, _ = _run(cmd, workspace, logfile, timeout=INSTALL_TIMEOUT)
    return code == 0


def _build_node(workspace: Path, logfile: Path) -> bool:
    pkg = _read_json(workspace / "package.json")
    scripts = pkg.get("scripts") or {}
    has_build = "build" in scripts

    if not _npm_install(workspace, logfile):
        _log(logfile, "npm install failed")
        return False

    if has_build:
        code, _ = _run(["npm", "run", "build"], workspace, logfile, timeout=BUILD_TIMEOUT)
        if code != 0:
            _log(logfile, "npm run build failed")
            return False
    if _static_root(workspace) is not None:
        _log(logfile, "static output detected; will be served directly")
        return True
    if any(k in scripts for k in ("start", "dev", "preview", "serve")):
        _log(logfile, "no static output; will launch the package's server script")
        return True
    _log(logfile, "no build output and no server script")
    return False


def _python_env_dir(workspace: Path) -> Path:
    return workspace / ".venv"


def _python_bin(workspace: Path) -> str:
    venv = _python_env_dir(workspace)
    candidate = venv / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return "python3"


def _build_python(workspace: Path, logfile: Path) -> bool:
    reqs = workspace / "requirements.txt"
    if reqs.exists():
        venv = _python_env_dir(workspace)
        if not venv.exists():
            code, _ = _run(["python3", "-m", "venv", str(venv)], workspace, logfile, timeout=180)
            if code != 0:
                _log(logfile, "venv creation failed; falling back to system python")
        python = _python_bin(workspace)
        code, _ = _run(
            [python, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
            workspace,
            logfile,
            timeout=INSTALL_TIMEOUT,
        )
        if code != 0:
            _log(logfile, "pip install failed")
            return False
    return True


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def start(app: AppRecord) -> dict:
    """Start the app if it needs a process. Returns fields to persist."""
    workspace = app.workspace
    logfile = app.log_path
    logfile.parent.mkdir(parents=True, exist_ok=True)
    logfile.write_text(f"# run log for {app.id} @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    mode = detect_mode(workspace)
    static_root = _static_root(workspace)

    if mode == "static" or (mode == "node" and static_root is not None):
        if static_root is None:
            raise RunnerError("no runnable output (no index.html found)")
        return {
            "mode": "static",
            "internal_port": None,
            "pid": None,
            "entry": _find_entry_html(workspace),
            "status": "running",
            "stage": "serving static site",
        }

    if mode == "node":
        return _start_node(app, workspace, logfile)

    return _start_python(app, workspace, logfile)


def _spawn(cmd: list[str], cwd: Path, logfile: Path, env: dict[str, str]) -> int:
    _log(logfile, f"\n$ {' '.join(cmd)}")
    fh = logfile.open("a")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env={**os.environ, **env},
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return proc.pid


def _wait_for_port(port: int, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def _start_node(app: AppRecord, workspace: Path, logfile: Path) -> dict:
    pkg = _read_json(workspace / "package.json")
    scripts = pkg.get("scripts") or {}
    port = free_port(config.APP_PORT_RANGE_START, config.APP_PORT_RANGE_END)
    env = {"PORT": str(port), "HOST": "0.0.0.0", "BROWSER": "none", "CI": "1"}

    script = None
    for candidate in ("start", "dev", "preview", "serve"):
        if candidate in scripts:
            script = candidate
            break
    if script is None:
        raise RunnerError("package.json has no start/dev/preview script")

    cmd = ["npm", "run", script, "--silent"]
    if script == "dev":
        cmd += ["--", "--host", "0.0.0.0", "--port", str(port)]
    pid = _spawn(cmd, workspace, logfile, env)
    if not _wait_for_port(port):
        _log(logfile, f"[server did not open port {port} in time]")
        raise RunnerError(f"server did not start on port {port}; see build log")
    return {
        "mode": "node",
        "internal_port": port,
        "pid": pid,
        "entry": "",
        "status": "running",
        "stage": f"node server on :{port}",
    }


def _start_python(app: AppRecord, workspace: Path, logfile: Path) -> dict:
    port = free_port(config.APP_PORT_RANGE_START, config.APP_PORT_RANGE_END)
    env = {"PORT": str(port), "HOST": "0.0.0.0", "PYTHONUNBUFFERED": "1"}
    python = _python_bin(workspace)

    for script in RUN_SCRIPTS:
        if (workspace / script).exists():
            os.chmod(workspace / script, 0o755)
            pid = _spawn(["bash", script], workspace, logfile, env)
            break
    else:
        if (workspace / "manage.py").exists():
            cmd = [python, "manage.py", "runserver", f"0.0.0.0:{port}"]
        else:
            entry = next(
                (name for name in PY_ENTRY_CANDIDATES if (workspace / name).exists()), None
            )
            if entry is None:
                raise RunnerError("no python entry point found")
            cmd = [python, entry]
        pid = _spawn(cmd, workspace, logfile, env)

    if not _wait_for_port(port):
        _log(logfile, f"[server did not open port {port} in time]")
        raise RunnerError(f"server did not start on port {port}; see run log")
    return {
        "mode": "python",
        "internal_port": port,
        "pid": pid,
        "entry": "",
        "status": "running",
        "stage": f"python server on :{port}",
    }


def stop(app: AppRecord) -> None:
    if not app.pid:
        return
    try:
        os.killpg(os.getpgid(app.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(app.pid, signal.SIGTERM)
        except OSError:
            pass


def tail_log(app: AppRecord, limit: int = 20000) -> str:
    text = ""
    for name in ("run.log", "build.log"):
        path = app.base_dir / "logs" / name
        if path.exists():
            text += path.read_text(errors="ignore")
    return text[-limit:]


def cleanup_workspace(app: AppRecord) -> None:
    shutil.rmtree(app.base_dir, ignore_errors=True)
