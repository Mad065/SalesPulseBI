"""Unit tests for the native desktop shell and bridge."""

from __future__ import annotations

import base64
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

from salespulsebi.desktop import DesktopBridge, find_free_port


def test_find_free_port_returns_preferred_when_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert find_free_port("127.0.0.1", port) == port


def test_find_free_port_falls_back_when_occupied() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        occupied = s.getsockname()[1]
        alt = find_free_port("127.0.0.1", occupied)
        assert alt != occupied
        assert alt > 0


def test_desktop_bridge_save_and_pick(tmp_path: Path, monkeypatch) -> None:
    out_file = tmp_path / "report.csv"

    class FakeWindow:
        def create_file_dialog(self, dialog_type, save_filename=None):
            if dialog_type == "SAVE":
                return str(out_file)
            return [str(tmp_path)]

    fake_webview = SimpleNamespace(FOLDER_DIALOG="FOLDER", SAVE_DIALOG="SAVE")
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    bridge = DesktopBridge()
    assert bridge.pick_folder() is None

    bridge.attach_window(FakeWindow())
    assert bridge.pick_folder() == str(tmp_path)

    payload = base64.b64encode(b"a,b\n1,2\n").decode("ascii")
    saved = bridge.save_file("report.csv", payload)
    assert saved == str(out_file)
    assert out_file.read_text(encoding="utf-8") == "a,b\n1,2\n"
