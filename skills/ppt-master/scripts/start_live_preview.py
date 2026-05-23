#!/usr/bin/env python3
"""
PPT Master - Start Live Preview

Start the SVG editor server as a background process, wait until it is ready,
and print the actual preview URL.

Usage:
    python3 scripts/start_live_preview.py <project_path> [options]

Examples:
    python3 scripts/start_live_preview.py projects/my-project
    python3 scripts/start_live_preview.py projects/my-project --no-browser
    python3 scripts/start_live_preview.py projects/my-project --port 5051

Dependencies:
    None (uses the repository's svg_editor/server.py entry point)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent.parent
SERVER_SCRIPT = SCRIPTS_DIR / "svg_editor" / "server.py"
LOCK_FILE_NAME = ".live_preview.lock"
GLOBAL_LOCK_FILE = Path.home() / ".ppt-master" / "live_preview_global.lock"


def _preview_url(port: int) -> str:
    """Return the canonical preview URL for *port*."""
    return f"http://127.0.0.1:{port}/?preview={int(time.time())}"


def _process_alive(pid: int) -> bool:
    """Return True when a pid still exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _lock_matches_project(lock: dict[str, Any], project_path: Path) -> bool:
    raw_project = str(lock.get("project_path") or "")
    if not raw_project:
        return False
    try:
        return Path(raw_project).resolve() == project_path
    except OSError:
        return False


def _probe_url(url: str, *, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 200) < 500
    except (OSError, TimeoutError, urllib.error.URLError, KeyboardInterrupt):
        return False


def _request_shutdown(port: int, *, timeout: float = 1.5) -> None:
    payload = json.dumps({"reason": "restart-unhealthy-preview"}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=timeout).close()
    except (OSError, TimeoutError, urllib.error.URLError, KeyboardInterrupt):
        pass


def _discard_unhealthy_matching_locks(project_path: Path) -> None:
    """Remove same-project locks that point to an unresponsive preview."""
    for lock_path in (project_path / LOCK_FILE_NAME, GLOBAL_LOCK_FILE):
        lock = _read_lock(lock_path)
        if not lock or not _lock_matches_project(lock, project_path):
            continue
        pid = int(lock.get("pid") or 0)
        port = int(lock.get("port") or 0)
        if pid > 0 and port > 0:
            url = _preview_url(port)
            if _process_alive(pid) and _probe_url(url):
                continue
            _request_shutdown(port)
        if pid > 0 and _process_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _ready_from_lock(project_path: Path) -> dict[str, Any] | None:
    lock_paths = (project_path / LOCK_FILE_NAME, GLOBAL_LOCK_FILE)
    for lock_path in lock_paths:
        lock = _read_lock(lock_path)
        if not lock:
            continue
        pid = int(lock.get("pid") or 0)
        port = int(lock.get("port") or 0)
        if not _lock_matches_project(lock, project_path):
            continue
        if not _process_alive(pid):
            continue
        if port <= 0:
            continue
        url = _preview_url(port)
        if _probe_url(url):
            return {"pid": pid, "port": port, "url": url}
    return None


def _tail(path: Path, *, max_chars: int = 2000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return text[-max_chars:]


def _spawn_server(
    project_path: Path,
    *,
    port: int,
    idle_timeout: int | None,
    log_path: Path,
) -> subprocess.Popen:
    command = [
        sys.executable,
        str(SERVER_SCRIPT),
        str(project_path),
        "--port",
        str(port),
        "--live",
        "--no-browser",
    ]
    if idle_timeout is not None:
        command.extend(["--timeout", str(idle_timeout)])

    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)

    with log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n--- start_live_preview {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_handle.write(" ".join(command) + "\n")
        log_handle.flush()
        return subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start PPT Master live preview in the background.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_path", help="Project directory to preview.")
    parser.add_argument("--port", type=int, default=5050, help="Preferred port (default: 5050).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically.")
    parser.add_argument("--wait", type=float, default=15.0, help="Seconds to wait for readiness (default: 15).")
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Idle timeout passed to server.py (default: server live-mode default).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_path = Path(args.project_path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        print(f"error: project directory does not exist: {project_path}", file=sys.stderr)
        return 1

    existing = _ready_from_lock(project_path)
    if existing:
        if not args.no_browser:
            webbrowser.open(existing["url"])
        print(f"LIVE_PREVIEW_URL={existing['url']}")
        print(f"LIVE_PREVIEW_PID={existing['pid']}")
        print("LIVE_PREVIEW_STATUS=reused")
        return 0
    _discard_unhealthy_matching_locks(project_path)

    log_path = project_path / ".preview" / "live_preview.log"
    process = _spawn_server(
        project_path,
        port=args.port,
        idle_timeout=args.timeout,
        log_path=log_path,
    )

    deadline = time.monotonic() + max(0.1, args.wait)
    ready: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        ready = _ready_from_lock(project_path)
        if ready:
            break
        if process.poll() is not None and ready is None:
            break
        time.sleep(0.2)

    if ready:
        if not args.no_browser:
            webbrowser.open(ready["url"])
        print(f"LIVE_PREVIEW_URL={ready['url']}")
        print(f"LIVE_PREVIEW_PID={ready['pid']}")
        print(f"LIVE_PREVIEW_LOG={log_path}")
        print("LIVE_PREVIEW_STATUS=started")
        return 0

    print("error: live preview did not become ready in time", file=sys.stderr)
    print(f"log: {log_path}", file=sys.stderr)
    tail = _tail(log_path)
    if tail:
        print(tail, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
