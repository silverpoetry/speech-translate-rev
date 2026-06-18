from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.app_startup_controller import AppStartupController


class FakeBridgeBinding:
    def __init__(self) -> None:
        self.bridge = None

    def get(self):
        return self.bridge

    def set(self, bridge) -> None:
        self.bridge = bridge


class FakeBridge:
    def __init__(self) -> None:
        self.tray = None
        self.window = None
        self.markers = []
        self.startup_t0 = None
        self.bound_window = None

    def set_startup_t0(self, started_at: float) -> None:
        self.startup_t0 = started_at

    def log_startup_marker(self, marker: str) -> None:
        self.markers.append(marker)

    def bind_window(self, window) -> None:
        self.bound_window = window

    def get_tray(self):
        return self.tray

    def bind_tray(self, tray) -> None:
        self.tray = tray


class FakeWindow:
    pass


class FakeSettings:
    def __init__(self, cache: dict[str, object]) -> None:
        self.cache = dict(cache)
        self.saved = []

    def save_key(self, key: str, value: object) -> None:
        self.saved.append((key, value))
        self.cache[key] = value


class FakeWebview:
    def __init__(self) -> None:
        self.create_calls = []
        self.start_calls = []

    def create_window(self, *args, **kwargs):
        self.create_calls.append((args, kwargs))
        return FakeWindow()

    def start(self, callback, debug=False):
        self.start_calls.append(debug)
        callback()


class AppStartupControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.fake_webview = FakeWebview()
        self.ffmpeg_calls = []
        self.log_levels = []
        self.bridge_binding = FakeBridgeBinding()
        self.settings = FakeSettings({"log_level": "INFO", "mw_size": "1140x680", "mw_pos": ""})
        self.controller = AppStartupController(
            bridge_factory=lambda: self.bridge,
            ffmpeg_path_adder=lambda weak=False: self.ffmpeg_calls.append(weak) or True,
            webview_loader=lambda: self.fake_webview,
            bridge_getter=self.bridge_binding.get,
            bridge_setter=self.bridge_binding.set,
            settings=self.settings,
        )

    def test_prepare_main_window_size_keeps_configured_size(self) -> None:
        self.settings.cache["mw_size"] = "1140x680"

        result = self.controller.prepare_main_window_size()

        self.assertEqual(result, "1140x680")
        self.assertEqual(self.settings.saved, [])

    def test_start_bootstraps_bridge_and_window(self) -> None:
        fake_tray_calls = []

        class FakeTray:
            def __init__(self, bridge):
                fake_tray_calls.append(bridge)

        with patch("speech_translate.app_startup_controller.AppTray", FakeTray):
            self.controller.start(with_log_init=True, log_initializer=lambda level: self.log_levels.append(level))

        self.assertEqual(self.log_levels, ["INFO"])
        self.assertEqual(self.ffmpeg_calls, [True])
        self.assertIsNotNone(self.bridge.startup_t0)
        self.assertEqual(self.fake_webview.start_calls, [False])
        self.assertIn("before_create_main_window", self.bridge.markers)
        self.assertEqual(len(fake_tray_calls), 1)
        self.assertIs(self.bridge_binding.bridge, self.bridge)

    def test_start_restores_saved_logical_position(self) -> None:
        self.settings.cache["mw_pos"] = "180,120"

        with patch("speech_translate.app_startup_controller.AppTray"):
            self.controller.start(with_log_init=False, log_initializer=None)

        _, kwargs = self.fake_webview.create_calls[0]
        self.assertEqual((kwargs["width"], kwargs["height"]), (1140, 680))
        self.assertEqual((kwargs["x"], kwargs["y"]), (180, 120))

    def test_start_disables_tray_when_flag_present(self) -> None:
        with patch(
            "speech_translate.app_startup_controller.sys.argv",
            ["app.py", "--no-tray"],
        ), patch("speech_translate.app_startup_controller.AppTray") as fake_tray:
            self.controller.start(with_log_init=False, log_initializer=None)

        fake_tray.assert_not_called()

    def test_start_enables_debug_mode_when_flag_present(self) -> None:
        with patch(
            "speech_translate.app_startup_controller.sys.argv",
            ["app.py", "--debug-webview"],
        ), patch("speech_translate.app_startup_controller.AppTray"):
            self.controller.start(with_log_init=False, log_initializer=None)

        self.assertEqual(self.fake_webview.start_calls[-1], True)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
