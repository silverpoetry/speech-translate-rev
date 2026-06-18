from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.detached_window_controller import DetachedWindowController


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {
            "ex_tc_geometry": "800x200",
            "tb_ex_tc_font": "Arial",
            "tb_ex_tc_font_size": 13,
            "tb_ex_tc_font_bold": True,
            "tb_ex_tc_font_color": "#FFFFFF",
            "tb_ex_tc_bg_color": "#000000",
            "ex_tc_always_on_top": 0,
            "ex_tc_no_title_bar": 0,
            "ex_tc_opacity": 1.0,
            "ex_tc_click_through": 0,
        }
        self.saved = {}

    def save_key(self, key: str, value):
        self.saved[key] = value
        self.cache[key] = value


class FakeBridge:
    def __init__(self) -> None:
        self.live_state = {"detached_transcribed_html": "<b>hello</b>"}

    def snapshot_live_state(self):
        return dict(self.live_state)


class FakeWindowManager:
    def __init__(self) -> None:
        self.windows = {}
        self.created = []
        self.closed = []
        self.shown = []
        self.hidden = []
        self.updated_content = []
        self.updated_config = []

    def create_window(self, mode, x, y, width, height):
        self.created.append((mode, x, y, width, height))
        self.windows[mode] = object()

    def has_window(self, mode):
        return mode in self.windows

    def get_window(self, mode):
        return self.windows.get(mode)

    def move_window(self, mode, x, y):
        return mode in self.windows

    def close_window(self, mode):
        self.closed.append(mode)
        self.windows.pop(mode, None)

    def show_window(self, mode):
        self.shown.append(mode)

    def hide_window(self, mode):
        self.hidden.append(mode)

    def update_window_content(self, mode, html):
        self.updated_content.append((mode, html))

    def update_window_config(self, mode, config):
        self.updated_config.append((mode, config))


class DetachedWindowControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = FakeSettings()
        self.bridge = FakeBridge()
        self.window_manager = FakeWindowManager()
        self.controller = DetachedWindowController(self.bridge, self.settings, self.window_manager)

    def test_set_detached_config_persists_mode_specific_key(self) -> None:
        result = self.controller.set_detached_config("tc", "font", "Consolas")
        self.assertEqual(result["key"], "tb_ex_tc_font")
        self.assertEqual(self.settings.saved["tb_ex_tc_font"], "Consolas")

    def test_create_detached_window_creates_window_and_pushes_live_content(self) -> None:
        result = self.controller.create_detached_window("tc", x=10, y=20)
        self.assertEqual(result["status"], "created")
        self.assertEqual(self.window_manager.created[0][0], "tc")
        self.assertEqual(self.window_manager.updated_content[-1], ("tc", "<b>hello</b>"))
        self.assertEqual(self.window_manager.updated_config[-1][0], "tc")

    def test_toggle_detached_window_closes_existing_window(self) -> None:
        self.window_manager.windows["tc"] = object()
        result = self.controller.toggle_detached_window("tc")
        self.assertEqual(result, {"status": "closed", "mode": "tc"})
        self.assertIn("tc", self.window_manager.closed)

    def test_update_detached_content_returns_missing_for_absent_window(self) -> None:
        result = self.controller.update_detached_content("tc", "hello")
        self.assertEqual(result, {"status": "missing", "mode": "tc"})

    def test_create_detached_window_normalizes_invalid_mode_to_default(self) -> None:
        self.bridge.live_state = {"detached_translated_html": "<i>world</i>"}
        result = self.controller.create_detached_window("invalid", x=5, y=6)
        self.assertEqual(result, {"status": "created", "mode": "tl"})
        self.assertEqual(self.window_manager.created[0][0], "tl")
        self.assertEqual(self.window_manager.updated_content[-1], ("tl", "<i>world</i>"))
        self.assertEqual(self.window_manager.updated_config[-1][0], "tl")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
