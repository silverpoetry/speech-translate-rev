from __future__ import annotations

import os
import sys
import types
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.app_tray import AppTray


class FakeWindow:
    def __init__(self) -> None:
        self.calls = []

    def restore(self):
        self.calls.append("restore")

    def show(self):
        self.calls.append("show")

    def bring_to_front(self):
        self.calls.append("front")


class FakeBridge:
    def __init__(self) -> None:
        self.directory_calls = []
        self.window = None
        self.quit_calls = 0

    def open_directory(self, name: str):
        self.directory_calls.append(name)
        return {"ok": True, "name": name}

    def get_window(self):
        return self.window

    def quit_app(self):
        self.quit_calls += 1


class FakePanelHost:
    def __init__(self) -> None:
        self.show_calls = []
        self.hide_calls = 0
        self.stop_calls = 0

    def show_at(self, x: int, y: int) -> None:
        self.show_calls.append((x, y))

    def hide(self) -> None:
        self.hide_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


class TrayInitGuard(AppTray):
    def _create_tray(self):
        self.icon = None

    def _create_panel_host(self):
        return FakePanelHost()


class AppTrayTests(unittest.TestCase):
    def test_init_does_not_precreate_panel_host(self) -> None:
        tray = TrayInitGuard(FakeBridge())
        self.assertIsNone(tray.panel_host)

    def test_open_panel_creates_host_lazily_and_delegates_coordinates(self) -> None:
        tray = TrayInitGuard(FakeBridge())
        tray._cursor_position = lambda: (1400, 900)

        tray.open_panel()

        self.assertIsNotNone(tray.panel_host)
        self.assertEqual(tray.panel_host.show_calls, [(1400, 900)])

    def test_hide_panel_and_stop_delegate_to_host(self) -> None:
        tray = TrayInitGuard(FakeBridge())
        host = tray._ensure_panel_host()

        tray.hide_panel()
        tray.stop()

        self.assertEqual(host.hide_calls, 1)
        self.assertEqual(host.stop_calls, 1)
        self.assertIsNone(tray.panel_host)

    def test_show_app_restores_and_shows_window(self) -> None:
        bridge = FakeBridge()
        bridge.window = FakeWindow()
        tray = TrayInitGuard(bridge)
        host = tray._ensure_panel_host()

        tray.show_app()

        self.assertEqual(host.hide_calls, 1)
        self.assertEqual(bridge.window.calls, ["restore", "show", "front"])

    def test_exit_app_delegates_to_bridge(self) -> None:
        bridge = FakeBridge()
        tray = TrayInitGuard(bridge)

        tray.exit_app()

        self.assertEqual(bridge.quit_calls, 1)

    def test_install_pointer_actions_replaces_win32_notify_handler(self) -> None:
        tray = AppTray.__new__(AppTray)
        tray.bridge = FakeBridge()
        open_calls = []
        show_calls = []
        tray.open_panel = lambda *_args: open_calls.append("panel")
        tray.show_app = lambda *_args: show_calls.append("show")
        original_calls = []
        win32 = types.SimpleNamespace(
            WM_NOTIFY=1035,
            WM_RBUTTONUP=517,
            WM_LBUTTONUP=514,
            WM_LBUTTONDBLCLK=515,
        )
        tray.icon = types.SimpleNamespace(
            _message_handlers={
                win32.WM_NOTIFY: lambda wparam, lparam: original_calls.append((wparam, lparam))
            }
        )

        original_module = sys.modules.get("pystray._util")
        sys.modules["pystray._util"] = types.SimpleNamespace(win32=win32)
        try:
            tray._install_pointer_actions()
        finally:
            if original_module is None:
                del sys.modules["pystray._util"]
            else:
                sys.modules["pystray._util"] = original_module

        handler = tray.icon._message_handlers[win32.WM_NOTIFY]
        handler(1, win32.WM_RBUTTONUP)
        handler(2, win32.WM_LBUTTONUP)
        handler(3, win32.WM_LBUTTONDBLCLK)
        handler(4, 999)

        self.assertEqual(open_calls, ["panel"])
        self.assertEqual(show_calls, ["show", "show"])
        self.assertEqual(original_calls, [(4, 999)])


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
