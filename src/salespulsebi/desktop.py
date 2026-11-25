"""Native desktop shell powered by pywebview with embedded Uvicorn server."""

from __future__ import annotations

import base64
import socket
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI


def find_free_port(host: str, preferred_port: int) -> int:
    """Return preferred_port if available, otherwise allocate an ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex((host, preferred_port)) != 0:
            return preferred_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_for_health(url: str, timeout: float = 10.0) -> bool:
    """Poll local health endpoint until the embedded server is ready."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


class DesktopBridge:
    """JS-to-Python bridge exposed to the SPA as `window.pywebview.api`."""

    def __init__(self) -> None:
        self._window: Any = None

    def attach_window(self, window: Any) -> None:
        self._window = window

    def pick_folder(self) -> str | None:
        """Open native OS folder picker dialog and return selected path."""
        if not self._window:
            return None
        import webview

        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            return str(result[0])
        return None

    def save_file(self, filename: str, base64_data: str) -> str | None:
        """Open native Save As dialog and write exported file bytes to disk."""
        if not self._window:
            return None
        import webview

        dest = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=filename,
        )
        if not dest:
            return None
        target = Path(dest[0] if isinstance(dest, (list, tuple)) else dest)
        target.write_bytes(base64.b64decode(base64_data))
        return str(target)


def run_desktop_app(app: FastAPI, *, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Start FastAPI in a background thread and open the native SalesPulseBI window."""
    import webview

    chosen_port = find_free_port(host, port)
    config = uvicorn.Config(app, host=host, port=chosen_port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://{host}:{chosen_port}"
    wait_for_health(f"{base_url}/api/v1/health", timeout=12.0)

    bridge = DesktopBridge()
    window = webview.create_window(
        title="SalesPulseBI",
        url=base_url,
        width=1360,
        height=860,
        min_size=(1024, 680),
        js_api=bridge,
    )
    bridge.attach_window(window)

    try:
        webview.start()
    finally:
        server.should_exit = True
        thread.join(timeout=3.0)
