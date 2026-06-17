from __future__ import annotations

import os
import sys
import types
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.app_tray import AppTray, TrayPanelApi


class FakeBridge:
    def __init__(self) -> None:
        self.directory_calls = []
        self.window = None

    def open_directory(self, name: str):
        self.directory_calls.append(name)
        return {"ok": True, "name": name}

    def get_window(self):
        return self.window

    def quit_app(self):
        return None


class TrayInitGuard(AppTray):
    def _create_tray(self):
        self.icon = None

    def _ensure_panel(self):
        raise AssertionError("panel should be lazy-created")


class AppTrayTests(unittest.TestCase):
    def test_init_does_not_precreate_panel(self) -> None:
        tray = TrayInitGuard(FakeBridge())
        self.assertIsNone(tray.panel_window)

    def test_tray_panel_api_keeps_only_bound_actions(self) -> None:
        bridge = FakeBridge()
        tray = AppTray.__new__(AppTray)
        tray.bridge = bridge
        hide_calls = []
        show_calls = []
        quit_calls = []
        tray.hide_panel = lambda: hide_calls.append("hide")
        tray.show_app = lambda *_args: show_calls.append("show")
        tray.exit_app = lambda *_args: quit_calls.append("quit")

        api = TrayPanelApi(tray)

        self.assertFalse(hasattr(api, "tray"))
        self.assertEqual(api.show_app(), {"ok": True})
        self.assertEqual(api.open_directory("log"), {"ok": True, "name": "log"})
        self.assertEqual(api.hide_panel(), {"ok": True})
        self.assertEqual(api.quit_app(), {"ok": True})
        self.assertEqual(show_calls, ["show"])
        self.assertEqual(quit_calls, ["quit"])
        self.assertEqual(bridge.directory_calls, ["log"])
        self.assertEqual(hide_calls, ["hide", "hide", "hide", "hide"])

    def test_install_pointer_actions_replaces_win32_notify_handler(self) -> None:
        bridge = FakeBridge()
        tray = AppTray.__new__(AppTray)
        tray.bridge = bridge
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
