from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.app_startup_controller import AppStartupController


class FakeBridge:
    def __init__(self) -> None:
        self.tray = None
        self.window = None
        self.markers = []
        self.startup_t0 = None
        self.bound_window = None

    def set_startup_t0(self, started_at: float) -> None:
        self.startup_t0 = started_at

    def _log_startup_marker(self, marker: str) -> None:
        self.markers.append(marker)

    def bind_window(self, window) -> None:
        self.bound_window = window

    def get_tray(self):
        return self.tray

    def bind_tray(self, tray) -> None:
        self.tray = tray


class FakeWindow:
    pass


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
        self.controller = AppStartupController(
            bridge_factory=lambda: self.bridge,
            ffmpeg_path_adder=lambda weak=False: self.ffmpeg_calls.append(weak) or True,
            webview_loader=lambda: self.fake_webview,
        )

    def test_prepare_main_window_size_migrates_legacy_default(self) -> None:
        with patch("speech_translate.app_startup_controller.sj.cache", {"mw_size": "1140x680"}), patch(
            "speech_translate.app_startup_controller.sj.save_key"
        ) as save_key:
            result = self.controller.prepare_main_window_size()
        self.assertEqual(result, "980x620")
        save_key.assert_called_once_with("mw_size", "980x620")

    def test_start_bootstraps_bridge_and_window(self) -> None:
        fake_tray_calls = []

        class FakeTray:
            def __init__(self, bridge):
                fake_tray_calls.append(bridge)

        with patch("speech_translate.app_startup_controller.sj.cache", {"log_level": "INFO", "mw_size": "980x620"}), patch(
            "speech_translate.app_startup_controller.AppTray", FakeTray
        ), patch("speech_translate.app_startup_controller.setattr") as fake_setattr:
            self.controller.start(with_log_init=True, log_initializer=lambda level: self.log_levels.append(level))

        self.assertEqual(self.log_levels, ["INFO"])
        self.assertEqual(self.ffmpeg_calls, [True])
        self.assertIsNotNone(self.bridge.startup_t0)
        self.assertEqual(self.fake_webview.start_calls, [False])
        self.assertIn("before_create_main_window", self.bridge.markers)
        self.assertEqual(len(fake_tray_calls), 1)
        fake_setattr.assert_any_call(unittest.mock.ANY, "web_bridge", self.bridge)

    def test_start_disables_tray_when_flag_present(self) -> None:
        with patch("speech_translate.app_startup_controller.sj.cache", {"log_level": "INFO", "mw_size": "980x620"}), patch(
            "speech_translate.app_startup_controller.sys.argv",
            ["app.py", "--no-tray"],
        ), patch("speech_translate.app_startup_controller.AppTray") as fake_tray:
            self.controller.start(with_log_init=False, log_initializer=None)

        fake_tray.assert_not_called()

    def test_start_enables_debug_mode_when_flag_present(self) -> None:
        with patch("speech_translate.app_startup_controller.sj.cache", {"log_level": "INFO", "mw_size": "980x620"}), patch(
            "speech_translate.app_startup_controller.sys.argv",
            ["app.py", "--debug-webview"],
        ), patch("speech_translate.app_startup_controller.AppTray"):
            self.controller.start(with_log_init=False, log_initializer=None)

        self.assertEqual(self.fake_webview.start_calls[-1], True)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
