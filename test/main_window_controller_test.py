from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from speech_translate.window_geometry import WindowPlacement

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
    def test_save_main_window_geometry_persists_logical_size_and_position_from_outer_bounds(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        controller = MainWindowController(bridge, settings)
        window = FakeWindow()
        window.native = SimpleNamespace(
            DeviceDpi=168,
            Bounds=SimpleNamespace(X=210, Y=245, Width=1995, Height=1190),
        )
        bridge.window = window

        controller.save_main_window_geometry()

        self.assertEqual(settings.saved["mw_size"], "1140x680")
        self.assertEqual(settings.saved["mw_pos"], "120,140")

    def test_show_main_window_marks_allowed_and_shows_window(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        controller = MainWindowController(bridge, settings)
        bridge.window = FakeWindow()
        controller.show_main_window()
        self.assertTrue(bridge.window.shown)
        self.assertTrue(bridge.window.brought)

    def test_show_main_window_restores_target_placement_before_show(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        controller = MainWindowController(bridge, settings)
        bridge.window = FakeWindow()
        bridge.window._speechtranslate_target_placement = WindowPlacement(width=1140, height=680, x=180, y=120)

        with patch("speech_translate.main_window_controller.apply_native_window_placement", return_value=True) as apply_placement:
            controller.show_main_window()

        apply_placement.assert_called_once()
        self.assertTrue(bridge.window.brought)
        self.assertTrue(bridge.window.shown)

    def test_hide_main_window_to_tray_requires_tray(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        controller = MainWindowController(bridge, settings)
        bridge.window = FakeWindow()

        result = controller.hide_main_window_to_tray()

        self.assertEqual(result["ok"], False)
        self.assertEqual(result["message"], "Tray not available")

    def test_hide_main_window_to_tray_hides_window_when_tray_available(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        bridge.tray = object()
        controller = MainWindowController(bridge, settings)
        bridge.window = FakeWindow()

        result = controller.hide_main_window_to_tray()

        self.assertEqual(result, {"ok": True})
        self.assertTrue(bridge.window.hidden)

    def test_save_main_window_geometry_skips_duplicate_save_without_force(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        controller = MainWindowController(bridge, settings)
        window = FakeWindow()
        window.native = SimpleNamespace(
            DeviceDpi=168,
            Bounds=SimpleNamespace(X=210, Y=245, Width=1995, Height=1190),
        )
        bridge.window = window

        controller.save_main_window_geometry()
        first_saved = dict(settings.saved)
        controller.save_main_window_geometry()

        self.assertEqual(settings.saved, first_saved)
        self.assertEqual(controller.main_geometry_last_saved, "1140x680@120,140")

    def test_bind_window_registers_event_handlers(self) -> None:
        settings = FakeSettings()
        bridge = FakeBridge()
        controller = MainWindowController(bridge, settings)
        window = FakeWindow()

        class EventHook(list):
            def __iadd__(self, other):
                self.append(other)
                return self

        window.events.shown = EventHook()
        window.events.loaded = EventHook()
        window.events.closed = EventHook()
        bridge.window = window

        controller.bind_window(window)

        self.assertEqual(len(window.events.shown), 1)
        self.assertEqual(len(window.events.loaded), 1)
        self.assertEqual(len(window.events.closed), 1)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
