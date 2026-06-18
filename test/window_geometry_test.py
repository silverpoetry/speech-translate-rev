from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.window_geometry import (
    clamp_window_position,
    center_window_pos,
    ensure_visible_or_center,
    extract_native_window_geometry,
    extract_window_placement,
    format_window_position,
    format_window_size,
    logical_to_native_size,
    logical_to_physical_point,
    inflate_window_request_for_style,
    offscreen_window_pos,
    native_to_logical_size,
    normalize_scale_factor,
    parse_window_position,
    parse_window_size,
    physical_to_logical_point,
    resolve_native_scale_factor,
    resolve_window_placement,
)


class FakeMetricsProvider:
    def __init__(
        self,
        screen_size: tuple[int, int] = (1920, 1080),
        virtual_bounds: tuple[int, int, int, int] = (0, 0, 1920, 1080),
        scale_factor: float = 1.0,
        platform_name: str = "Linux",
    ) -> None:
        self._screen_size = screen_size
        self._virtual_bounds = virtual_bounds
        self._scale_factor = scale_factor
        self._platform_name = platform_name

    def platform_name(self) -> str:
        return self._platform_name

    def screen_size(self) -> tuple[int, int]:
        return self._screen_size

    def virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        return self._virtual_bounds

    def scale_factor(self) -> float:
        return self._scale_factor


class WindowGeometryTests(unittest.TestCase):
    def test_parse_window_size_uses_defaults_on_invalid_input(self) -> None:
        self.assertEqual(parse_window_size("invalid", 980, 620), (980, 620))

    def test_parse_window_size_applies_minimums(self) -> None:
        self.assertEqual(parse_window_size("20x30", 980, 620), (320, 180))

    def test_parse_window_position_reads_logical_pair(self) -> None:
        self.assertEqual(parse_window_position("120, 340"), (120, 340))
        self.assertEqual(parse_window_position("invalid"), (None, None))

    def test_formatters_use_single_persisted_contract(self) -> None:
        self.assertEqual(format_window_size(640, 320), "640x320")
        self.assertEqual(format_window_position(120, 340), "120,340")

    def test_center_window_pos_centers_using_virtual_bounds(self) -> None:
        metrics = FakeMetricsProvider(virtual_bounds=(100, 50, 1000, 800))
        self.assertEqual(center_window_pos(400, 200, metrics=metrics), (400, 350))

    def test_offscreen_window_pos_places_window_beyond_virtual_right_edge(self) -> None:
        metrics = FakeMetricsProvider(virtual_bounds=(100, 50, 1000, 800))
        x, y = offscreen_window_pos(400, 200, metrics=metrics)
        self.assertGreater(x, 1100)
        self.assertEqual(y, 350)

    def test_ensure_visible_or_center_recenters_offscreen_window(self) -> None:
        metrics = FakeMetricsProvider(virtual_bounds=(0, 0, 1280, 720))
        self.assertEqual(ensure_visible_or_center(2000, 2000, 400, 300, metrics=metrics), (440, 210))

    def test_ensure_visible_or_center_keeps_partially_visible_window(self) -> None:
        metrics = FakeMetricsProvider(virtual_bounds=(0, 0, 1280, 720))
        self.assertEqual(ensure_visible_or_center(-200, 20, 400, 300, metrics=metrics), (-200, 20))

    def test_resolve_window_placement_keeps_visible_coordinates(self) -> None:
        metrics = FakeMetricsProvider(virtual_bounds=(0, 0, 1280, 720))
        placement = resolve_window_placement("640x360", 980, 620, x=80, y=40, metrics=metrics)
        self.assertEqual((placement.width, placement.height, placement.x, placement.y), (640, 360, 80, 40))

    def test_resolve_window_placement_uses_saved_logical_position(self) -> None:
        metrics = FakeMetricsProvider(virtual_bounds=(0, 0, 1280, 720))
        placement = resolve_window_placement("640x360", 980, 620, raw_position="180,120", metrics=metrics)
        self.assertEqual((placement.width, placement.height, placement.x, placement.y), (640, 360, 180, 120))

    def test_windows_size_is_clamped_to_screen(self) -> None:
        metrics = FakeMetricsProvider(
            screen_size=(800, 600),
            virtual_bounds=(0, 0, 800, 600),
            platform_name="Windows",
        )
        self.assertEqual(parse_window_size("2000x2000", 980, 620, metrics=metrics), (720, 480))

    def test_normalize_scale_factor_defaults_when_invalid(self) -> None:
        self.assertEqual(normalize_scale_factor(0), 1.0)
        self.assertEqual(normalize_scale_factor(None), 1.0)

    def test_logical_to_native_size_applies_scale_factor(self) -> None:
        self.assertEqual(logical_to_native_size(158, 172, scale_factor=2.25), (356, 387))

    def test_native_to_logical_size_applies_scale_factor(self) -> None:
        self.assertEqual(native_to_logical_size(2277, 1217, scale_factor=2.25), (1012, 541))

    def test_logical_to_physical_point_applies_scale_factor(self) -> None:
        self.assertEqual(logical_to_physical_point(680, 400, scale_factor=2.25), (1530, 900))

    def test_physical_to_logical_point_applies_scale_factor(self) -> None:
        self.assertEqual(physical_to_logical_point(1530, 900, scale_factor=2.25), (680, 400))

    def test_inflate_window_request_for_style_uses_style_transition_delta(self) -> None:
        with patch(
            "speech_translate.window_geometry.measure_style_frame_delta",
            side_effect=[(14, 37), (0, 0)],
        ):
            self.assertEqual(inflate_window_request_for_style(900, 240), (914, 277))

    def test_clamp_window_position_keeps_popup_within_logical_bounds(self) -> None:
        metrics = FakeMetricsProvider(
            screen_size=(1280, 720),
            virtual_bounds=(0, 0, 1280, 720),
            scale_factor=2.0,
            platform_name="Windows",
        )
        self.assertEqual(clamp_window_position(1300, 800, 152, 168, metrics=metrics), (1128, 552))

    def test_resolve_native_scale_factor_prefers_device_dpi(self) -> None:
        native_window = type("NativeWindow", (), {"DeviceDpi": 168})()
        self.assertEqual(resolve_native_scale_factor(native_window), 1.75)

    def test_extract_native_window_geometry_returns_logical_outer_bounds(self) -> None:
        native_window = type(
            "NativeWindow",
            (),
            {
                "DeviceDpi": 168,
                "Bounds": type("Bounds", (), {"X": 210, "Y": 245, "Width": 1575, "Height": 420})(),
            },
        )()

        geometry = extract_native_window_geometry(native_window)

        self.assertEqual((geometry.width, geometry.height), (900, 240))
        self.assertEqual((geometry.x, geometry.y), (120, 140))
        self.assertEqual((geometry.raw_x, geometry.raw_y), (210, 245))
        self.assertEqual((geometry.raw_width, geometry.raw_height), (1575, 420))
        self.assertEqual(geometry.scale_factor, 1.75)
        self.assertEqual(geometry.source, "bounds")

    def test_extract_window_placement_requires_native_outer_bounds(self) -> None:
        window = type("Window", (), {"native": None})()
        with self.assertRaises(RuntimeError):
            extract_window_placement(window)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
