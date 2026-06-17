from __future__ import annotations

import ctypes
import os
import sys
import types
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.app_tray import AppTray, TrayPanelApi


class FakeWindow:
    def __init__(self) -> None:
        self.calls = []
        self.native = types.SimpleNamespace(
            ShowInTaskbar=True,
            TopMost=False,
            Activate=lambda: self.calls.append("activate"),
            Handle=types.SimpleNamespace(ToInt32=lambda: 10),
        )

    def move(self, x: int, y: int):
        self.calls.append(("move", x, y))

    def restore(self):
        self.calls.append("restore")

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def destroy(self):
        self.calls.append("destroy")


class FakeBridgeWindow:
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


class FakeWebviewModule:
    def __init__(self, fake_window: FakeWindow) -> None:
        self.fake_window = fake_window
        self.calls = []

    def create_window(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.fake_window


class TrayInitGuard(AppTray):
    def _create_tray(self):
        self.icon = None


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

    def test_ensure_panel_creates_webview_window_lazily(self) -> None:
        fake_panel = FakeWindow()
        fake_webview = FakeWebviewModule(fake_panel)
        tray = TrayInitGuard(FakeBridge())
        tray._cursor_position = lambda: (1000, 700)
        tray._apply_panel_native_settings = lambda window: window.calls.append("native-settings")

        original = sys.modules.get("speech_translate.webview_runtime")
        import speech_translate.app_tray as app_tray_module

        load_webview_runtime_original = app_tray_module.load_webview_runtime
        app_tray_module.load_webview_runtime = lambda: fake_webview
        try:
            panel = tray._ensure_panel()
        finally:
            app_tray_module.load_webview_runtime = load_webview_runtime_original
            if original is not None:
                sys.modules["speech_translate.webview_runtime"] = original

        self.assertIs(panel, fake_panel)
        self.assertEqual(len(fake_webview.calls), 1)
        _args, kwargs = fake_webview.calls[0]
        self.assertEqual(kwargs["width"], tray.PANEL_WIDTH)
        self.assertEqual(kwargs["height"], tray.PANEL_HEIGHT)
        self.assertTrue(kwargs["hidden"])
        self.assertEqual(fake_panel.calls, ["native-settings"])

    def test_open_panel_moves_and_shows_window(self) -> None:
        fake_panel = FakeWindow()
        tray = TrayInitGuard(FakeBridge())
        tray.panel_window = fake_panel
        tray._cursor_position = lambda: (1200, 820)

        original_get = ctypes.windll.user32.GetWindowLongW if hasattr(ctypes.windll.user32, "GetWindowLongW") else None
        original_set = ctypes.windll.user32.SetWindowLongW if hasattr(ctypes.windll.user32, "SetWindowLongW") else None
        original_pos = ctypes.windll.user32.SetWindowPos if hasattr(ctypes.windll.user32, "SetWindowPos") else None
        ctypes.windll.user32.GetWindowLongW = lambda *_args: 0
        ctypes.windll.user32.SetWindowLongW = lambda *_args: 0
        ctypes.windll.user32.SetWindowPos = lambda *_args: 0
        try:
            tray.open_panel()
        finally:
            if original_get is not None:
                ctypes.windll.user32.GetWindowLongW = original_get
            if original_set is not None:
                ctypes.windll.user32.SetWindowLongW = original_set
            if original_pos is not None:
                ctypes.windll.user32.SetWindowPos = original_pos

        self.assertIn("restore", fake_panel.calls)
        self.assertIn("show", fake_panel.calls)
        self.assertIn("activate", fake_panel.calls)
        self.assertTrue(any(isinstance(call, tuple) and call[0] == "move" for call in fake_panel.calls))

    def test_show_app_restores_and_shows_main_window(self) -> None:
        bridge = FakeBridge()
        bridge.window = FakeBridgeWindow()
        tray = TrayInitGuard(bridge)

        tray.show_app()

        self.assertEqual(bridge.window.calls, ["restore", "show", "front"])

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

    def test_exit_app_delegates_to_bridge(self) -> None:
        bridge = FakeBridge()
        tray = TrayInitGuard(bridge)

        tray.exit_app()

        self.assertEqual(bridge.quit_calls, 1)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
