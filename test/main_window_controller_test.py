from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.main_window_controller import MainWindowController


class FakeSettings:
    def __init__(self) -> None:
        self.saved = {}
        self.cache = {}

    def save_key(self, key: str, value):
        self.saved[key] = value


class FakeWindow:
    def __init__(self) -> None:
        self.width = 900
        self.height = 620
        self.destroyed = False
        self.shown = False
        self.brought = False
        self.hidden = False
        self.native = None
        self.events = type("Events", (), {})()

    def show(self):
        self.shown = True

    def bring_to_front(self):
        self.brought = True

    def hide(self):
        self.hidden = True

    def destroy(self):
        self.destroyed = True


class FakeBridge:
    def __init__(self) -> None:
        self.window = None
        self.tray = None
        self.detached_window_manager = type("Detached", (), {"close_all": lambda self: None})()

    def get_window(self):
        return self.window

    def get_tray(self):
        return self.tray


class MainWindowControllerTests(unittest.TestCase):
    def test_save_main_window_geometry_uses_fallback_window_size(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        controller = MainWindowController(bridge, settings)
        bridge.window = FakeWindow()
        controller.save_main_window_geometry()
        self.assertEqual(settings.saved["mw_size"], "900x620")

    def test_show_main_window_marks_allowed_and_shows_window(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        controller = MainWindowController(bridge, settings)
        bridge.window = FakeWindow()
        controller.show_main_window()
        self.assertTrue(bridge.window.shown)
        self.assertTrue(bridge.window.brought)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
