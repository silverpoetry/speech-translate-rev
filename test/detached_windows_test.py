from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import call, patch

from speech_translate.window_geometry import WindowPlacement
from speech_translate.window_lifecycle import WindowLifecycleState

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
from speech_translate.detached_window_geometry import (
    persist_detached_window_placement,
    resolve_detached_window_placement,
)
from speech_translate.detached_window_settings import build_detached_window_settings


class DetachedWindowHelpersTests(unittest.TestCase):
    def test_manager_create_window_uses_injected_webview_loader(self) -> None:
        class EventHook:
            def __iadd__(self, callback):
                return self

        class FakeWindow:
            def __init__(self) -> None:
                self.events = SimpleNamespace(closing=EventHook(), closed=EventHook(), loaded=EventHook())
                self.native = None
                self.show_calls = 0
                self.bring_calls = 0

            def show(self) -> None:
                self.show_calls += 1
                return None

            def bring_to_front(self) -> None:
                self.bring_calls += 1
                return None

        class FakeWebview:
            def __init__(self) -> None:
                self.calls = []

            def create_window(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return FakeWindow()

        fake_webview = FakeWebview()
        manager = DetachedWindowManager(
            settings=type("Settings", (), {"cache": {"ex_tc_geometry": "900x240", "ex_tc_pos": "10,20"}})(),
            webview_loader=lambda: fake_webview,
        )

        @contextmanager
        def fake_preload_window_creation(_placement):
            yield type(
                "Plan",
                (),
                {"offscreen_placement": WindowPlacement(width=700, height=300, x=2610, y=140)},
            )()

        with patch("speech_translate.window_factory.preload_window_creation", fake_preload_window_creation):
            window = manager.create_window("tc", x=10, y=20, width=700, height=300)

        self.assertIsNotNone(window)
        self.assertIs(manager.windows["tc"], window)
        self.assertEqual(len(fake_webview.calls), 1)
        args, kwargs = fake_webview.calls[0]
        self.assertEqual(args[0], "Speech Translate - Transcribed")
        self.assertIn("mode=tc", args[1])
        self.assertNotIn("outerWidth", args[1])
        self.assertEqual(kwargs["width"], 700)
        self.assertEqual(kwargs["height"], 300)
        self.assertEqual((kwargs["x"], kwargs["y"]), (2610, 140))
        self.assertEqual(kwargs["hidden"], False)

    def test_manager_create_window_queues_native_contract_when_no_title_bar_enabled(self) -> None:
        class EventHook:
            def __iadd__(self, callback):
                return self

        class FakeWindow:
            def __init__(self) -> None:
                self.events = SimpleNamespace(closing=EventHook(), closed=EventHook(), loaded=EventHook())
                self.native = None

            def show(self) -> None:
                return None

            def bring_to_front(self) -> None:
                return None

        class FakeWebview:
            def __init__(self) -> None:
                self.calls = []

            def create_window(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return FakeWindow()

        fake_webview = FakeWebview()
        manager = DetachedWindowManager(
            settings=type(
                "Settings",
                (),
                {"cache": {"ex_tc_geometry": "900x240", "ex_tc_pos": "10,20", "ex_tc_no_title_bar": 1}},
            )(),
            webview_loader=lambda: fake_webview,
        )

        @contextmanager
        def fake_preload_window_creation(_placement):
            yield type(
                "Plan",
                (),
                {"offscreen_placement": WindowPlacement(width=700, height=300, x=2610, y=140)},
            )()

        with (
            patch("speech_translate.window_factory.preload_window_creation", fake_preload_window_creation),
            patch(
                "speech_translate.detached_windows.build_detached_native_contract",
                return_value={"kind": "detached_window"},
            ) as build_contract,
            patch("speech_translate.window_factory.set_pending_window_contract") as set_contract,
        ):
            manager.create_window("tc", x=10, y=20, width=700, height=300)

        _, kwargs = fake_webview.calls[0]
        self.assertEqual(kwargs["width"], 700)
        self.assertEqual(kwargs["height"], 300)
        self.assertEqual(kwargs["frameless"], False)
        self.assertEqual(kwargs["easy_drag"], True)
        build_contract.assert_called_once()
        self.assertEqual(set_contract.call_args_list, [call({"kind": "detached_window"}), call(None)])

    def test_mark_window_content_ready_reveals_preloaded_window(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.events = SimpleNamespace()
                self.native = None
                self._speechtranslate_window_lifecycle = WindowLifecycleState(
                    target_placement=WindowPlacement(width=700, height=300, x=10, y=20),
                    offscreen_placement=WindowPlacement(width=700, height=300, x=2610, y=140),
                )

        manager = DetachedWindowManager(settings=None)
        window = FakeWindow()
        manager.windows["tc"] = window
        manager.runtime.mark_window_loaded("tc", True)

        with patch("speech_translate.detached_windows.reveal_preloaded_window", return_value=True) as reveal_window:
            manager.mark_window_content_ready("tc")

        reveal_window.assert_called_once_with(window, bring_to_front=False)

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
                "limit_max": False,
                "limit_max_per_line": False,
                "max": 120,
                "max_per_line": 30,
                "use_conf_color": True,
                "always_on_top": 1,
                "no_title_bar": 1,
                "opacity": 0.7,
                "click_through": 1,
            },
        )

    def test_build_detached_window_settings_collects_geometry_and_position(self) -> None:
        settings_view = build_detached_window_settings(
            {
                "ex_tl_geometry": "640x320",
                "ex_tl_pos": "180,120",
                "tb_ex_tl_font": "Arial",
                "tb_ex_tl_font_size": 15,
            },
            "invalid",
        )

        self.assertEqual(settings_view.mode, "tl")
        self.assertEqual(settings_view.geometry_cache, "640x320")
        self.assertEqual(settings_view.position_cache, "180,120")
        self.assertEqual(settings_view.config.to_payload()["font"], "Arial")
        self.assertEqual(settings_view.config.to_payload()["font_size"], 15)
        self.assertEqual(settings_view.config.to_payload()["max"], 120)

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

        manager = type(
            "Manager",
            (),
            {
                "windows": {"tc": FakeWindow()},
                "has_window": lambda self, mode: mode in self.windows,
                "move_window": lambda self, mode, x, y: (self.windows[mode].move(x, y) or True)
                if mode in self.windows
                else False,
                "mark_window_content_ready": lambda self, mode: None,
            },
        )()
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
        width, height, x, y = resolve_detached_window_placement(
            None,
            "tc",
            x=10,
            y=20,
            width=640,
            height=320,
        )
        self.assertEqual((width, height, x, y), (640, 320, 10, 20))

    def test_manager_resolve_window_placement_uses_cached_geometry_and_position(self) -> None:
        settings = type("Settings", (), {"cache": {"ex_tc_geometry": "640x320", "ex_tc_pos": "180,120"}})()
        width, height, x, y = resolve_detached_window_placement(
            settings,
            "tc",
            x=None,
            y=None,
            width=None,
            height=None,
        )
        self.assertEqual((width, height, x, y), (640, 320, 180, 120))

    def test_manager_resolve_window_placement_allows_width_override_only(self) -> None:
        settings = type("Settings", (), {"cache": {"ex_tc_geometry": "640x320", "ex_tc_pos": "30,40"}})()
        width, height, x, y = resolve_detached_window_placement(
            settings,
            "tc",
            x=None,
            y=None,
            width=800,
            height=None,
        )
        self.assertEqual((width, height, x, y), (800, 320, 30, 40))

    def test_manager_resolve_window_placement_allows_height_override_only(self) -> None:
        settings = type("Settings", (), {"cache": {"ex_tc_geometry": "640x320", "ex_tc_pos": "30,40"}})()
        width, height, x, y = resolve_detached_window_placement(
            settings,
            "tc",
            x=None,
            y=None,
            width=None,
            height=500,
        )
        self.assertEqual((width, height, x, y), (640, 500, 30, 40))

    def test_manager_update_window_content_skips_duplicate_payload_after_send(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.scripts = []

            def evaluate_js(self, script: str):
                self.scripts.append(script)
                return None

        manager = DetachedWindowManager(settings=None)
        manager.windows["tc"] = FakeWindow()
        manager.runtime.mark_window_loaded("tc", True)
        manager.runtime.mark_window_content_ready("tc", True)

        manager.update_window_content("tc", "hello")
        manager.update_window_content("tc", "hello")

        self.assertEqual(len(manager.windows["tc"].scripts), 1)

    def test_manager_update_window_config_skips_duplicate_payload_after_send(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.scripts = []
                self.native = None

            def evaluate_js(self, script: str):
                self.scripts.append(script)
                return None

        manager = DetachedWindowManager(settings=None)
        manager.windows["tc"] = FakeWindow()
        manager.runtime.mark_window_loaded("tc", True)

        config = {"font": "Arial", "opacity": 1.0}
        manager.update_window_config("tc", config)
        manager.update_window_config("tc", config)

        self.assertEqual(len(manager.windows["tc"].scripts), 1)

    def test_manager_persist_window_geometry_uses_shared_native_outer_geometry_logic(self) -> None:
        class FakeSettings:
            def __init__(self) -> None:
                self.saved = {}

            def save_key(self, key: str, value: object) -> None:
                self.saved[key] = value

        class FakeWindow:
            def __init__(self) -> None:
                self.native = SimpleNamespace(
                    DeviceDpi=168,
                    Bounds=SimpleNamespace(X=210, Y=245, Width=1575, Height=420),
                )

        settings = FakeSettings()
        persist_detached_window_placement(settings, "tc", FakeWindow())

        self.assertEqual(settings.saved["ex_tc_geometry"], "900x240")
        self.assertEqual(settings.saved["ex_tc_pos"], "120,140")

    def test_manager_persist_window_geometry_reports_missing_native_geometry(self) -> None:
        class FakeSettings:
            def __init__(self) -> None:
                self.saved = {}

            def save_key(self, key: str, value: object) -> None:
                self.saved[key] = value

        class FakeWindow:
            native = None

        settings = FakeSettings()
        persist_detached_window_placement(settings, "tc", FakeWindow())
        self.assertEqual(settings.saved, {})


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
