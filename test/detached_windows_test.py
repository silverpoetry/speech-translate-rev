from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.detached_windows import (
    DetachedWindowApi,
    DetachedWindowManager,
    RecordingWindowApi,
    build_detached_config,
    detached_setting_key,
    get_detached_live_content,
    normalize_detached_mode,
)


class DetachedWindowHelpersTests(unittest.TestCase):
    def test_normalize_detached_mode_defaults_invalid_mode(self) -> None:
        self.assertEqual(normalize_detached_mode("invalid"), "tl")
        self.assertEqual(normalize_detached_mode("TC"), "tc")

    def test_build_detached_config_reads_mode_specific_keys(self) -> None:
        settings_cache = {
            "tb_ex_tc_font": "Consolas",
            "tb_ex_tc_font_size": 18,
            "tb_ex_tc_font_bold": False,
            "tb_ex_tc_font_color": "#00FF00",
            "tb_ex_tc_bg_color": "#101010",
            "ex_tc_always_on_top": 1,
            "ex_tc_no_title_bar": 1,
            "ex_tc_opacity": 0.7,
            "ex_tc_click_through": 1,
        }
        config = build_detached_config(settings_cache, "tc")
        self.assertEqual(
            config,
            {
                "font": "Consolas",
                "font_size": 18,
                "font_bold": False,
                "font_color": "#00FF00",
                "bg_color": "#101010",
                "always_on_top": 1,
                "no_title_bar": 1,
                "opacity": 0.7,
                "click_through": 1,
            },
        )

    def test_detached_setting_key_routes_window_flags_and_text_settings(self) -> None:
        self.assertEqual(detached_setting_key("tc", "always_on_top"), "ex_tc_always_on_top")
        self.assertEqual(detached_setting_key("tc", "font"), "tb_ex_tc_font")

    def test_get_detached_live_content_prefers_html_then_text(self) -> None:
        live_state = {
            "detached_transcribed_html": "<b>hello</b>",
            "detached_transcribed_text": "hello",
            "detached_translated_text": "world",
        }
        self.assertEqual(get_detached_live_content("tc", live_state), "<b>hello</b>")
        self.assertEqual(get_detached_live_content("tl", live_state), "world")
        self.assertIsNone(get_detached_live_content("invalid", {}))

    def test_detached_window_api_moves_window_with_numeric_payload(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.moves = []

            def move(self, x: int, y: int) -> None:
                self.moves.append((x, y))

        manager = type("Manager", (), {"windows": {"tc": FakeWindow()}, "mark_window_content_ready": lambda self, mode: None})()
        api = DetachedWindowApi(manager)
        result = api.move_detached_window("tc", "10.4", 20)
        self.assertEqual(result, {"status": "moved", "mode": "tc", "x": 10, "y": 20})
        self.assertEqual(manager.windows["tc"].moves, [(10, 20)])

    def test_detached_window_api_reports_ready(self) -> None:
        class FakeManager:
            def __init__(self) -> None:
                self.windows = {"tc": object()}
                self.ready_modes = []

            def mark_window_content_ready(self, mode: str) -> None:
                self.ready_modes.append(mode)

        manager = FakeManager()
        api = DetachedWindowApi(manager)
        result = api.detached_window_ready("TC")
        self.assertEqual(result, {"status": "ready", "mode": "tc"})
        self.assertEqual(manager.ready_modes, ["tc"])

    def test_recording_window_api_returns_provider_snapshot(self) -> None:
        api = RecordingWindowApi(lambda: {"status": "Recording", "active": True})
        self.assertEqual(api.get_recording_state(), {"status": "Recording", "active": True})

    def test_manager_resolve_window_placement_preserves_explicit_coordinates(self) -> None:
        manager = DetachedWindowManager(settings=None)
        width, height, x, y = manager._resolve_window_placement(
            "tc",
            x=10,
            y=20,
            width=640,
            height=320,
        )
        self.assertEqual((width, height, x, y), (640, 320, 10, 20))

    def test_manager_resolve_window_placement_uses_cached_geometry_defaults(self) -> None:
        settings = type("Settings", (), {"cache": {"ex_tc_geometry": "640x320"}})()
        manager = DetachedWindowManager(settings=settings)
        width, height, x, y = manager._resolve_window_placement(
            "tc",
            x=None,
            y=None,
            width=None,
            height=None,
        )
        self.assertEqual((width, height), (640, 320))
        self.assertIsInstance(x, int)
        self.assertIsInstance(y, int)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
