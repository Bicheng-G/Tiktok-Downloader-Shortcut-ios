"""Windows background launcher. No extra packages or administrator rights needed."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import json

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "artifacts" / "backend.log"


def load_env(path):
    # Literal values only: never execute a .env as shell code.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key.isidentifier() or not key.isascii():
            raise ValueError("Invalid .env entry")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


class StopEvent:
    """Kernel-owned lifetime prevents stale PID files or killing a reused PID."""

    def __init__(self, port):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateEventW.restype = wintypes.HANDLE
        self.api.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.OpenEventW.restype = wintypes.HANDLE
        self.api.SetEvent.argtypes = [wintypes.HANDLE]
        self.api.SetEvent.restype = wintypes.BOOL
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        identity = hashlib.sha256(f"{str(ROOT).lower()}:{port}".encode()).hexdigest()[:24]
        self.name = "Local\\VideoResolver-" + identity

    def open(self):
        handle = self.api.OpenEventW(0x100002, False, self.name)
        if not handle and ctypes.get_last_error() != 2:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def exists(self):
        handle = self.open()
        if handle:
            self.api.CloseHandle(handle)
        return bool(handle)


def healthy(port):
    try:
        # Loopback probes must not go through an inherited HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return json.load(response).get("status") == "ok"
    except (OSError, ValueError):
        return False


def run(port, env_file, event):
    ctypes.set_last_error(0)
    handle = event.api.CreateEventW(None, True, False, event.name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        event.api.CloseHandle(handle)
        return
    try:
        LOG.parent.mkdir(exist_ok=True)
        handler = RotatingFileHandler(LOG, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
        load_env(env_file)
        sys.path.insert(0, str(ROOT))
        import uvicorn
        from douyin_resolver.server import create_app

        app = create_app()
        # Bind before exposing readiness; a busy port must fail rather than
        # accidentally reporting another backend as our successfully started one.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
            config = uvicorn.Config(app, host="127.0.0.1", port=port, workers=1,
                                    access_log=False, log_config=None,
                                    timeout_graceful_shutdown=90)
            server = uvicorn.Server(config)

            def watch_stop():
                if event.api.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
                    server.should_exit = True

            watcher = threading.Thread(target=watch_stop, daemon=True)
            watcher.start()
            try:
                server.run(sockets=[listener])
            finally:
                event.api.SetEvent(handle)
                watcher.join(timeout=2)
    except Exception as exc:
        # Config errors can include secrets. Only record the exception type.
        logging.getLogger(__name__).error("Backend failed (%s). Check configuration and dependencies.", type(exc).__name__)
        raise SystemExit(1) from None
    finally:
        event.api.CloseHandle(handle)


def start(port, env_file, event):
    if event.exists():
        print("Backend is running." if healthy(port) else "Backend is starting or stopping. Try status shortly.")
        return
    if not env_file.is_file():
        raise ValueError(f"Missing configuration: {env_file}")
    # Validate settings without starting the bot or opening any network connection.
    load_env(env_file)
    sys.path.insert(0, str(ROOT))
    from douyin_resolver.server import create_app
    create_app()
    LOG.parent.mkdir(exist_ok=True)
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "run", "--port", str(port), "--env-file", str(env_file)],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"Backend did not start. Check {LOG}")
        if event.exists() and healthy(port):
            # Allow an immediate bind failure to be observed before success.
            time.sleep(0.2)
            if child.poll() is None:
                print(f"Backend started in background: http://127.0.0.1:{port}")
                return
        time.sleep(0.2)
    raise RuntimeError(f"Startup is still pending. Check status and {LOG}")


def stop(event):
    handle = event.open()
    if not handle:
        print("Backend is stopped.")
        return
    try:
        if not event.api.SetEvent(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        event.api.CloseHandle(handle)
    deadline = time.monotonic() + 100
    while event.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if event.exists():
        raise RuntimeError(f"Shutdown is still pending. Check {LOG}")
    print("Backend stopped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "restart", "status", "run"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This launcher requires Windows")
    event = StopEvent(args.port)
    try:
        if args.action == "run":
            run(args.port, args.env_file.resolve(), event)
        elif args.action == "status":
            running = event.exists()
            print("Backend: " + ("running" if running and healthy(args.port) else "starting/stopping" if running else "stopped"))
            print(f"Log: {LOG}")
        else:
            if args.action in ("stop", "restart"):
                stop(event)
            if args.action in ("start", "restart"):
                start(args.port, args.env_file.resolve(), event)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
