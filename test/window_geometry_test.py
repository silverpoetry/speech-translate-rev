from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.window_geometry import (
    center_window_pos,
    ensure_visible_or_center,
    extract_native_window_geometry,
    logical_to_native_size,
    native_to_logical_size,
    normalize_scale_factor,
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

    def test_center_window_pos_centers_using_virtual_bounds(self) -> None:
        metrics = FakeMetricsProvider(virtual_bounds=(100, 50, 1000, 800))
        self.assertEqual(center_window_pos(400, 200, metrics=metrics), (400, 350))

    def test_center_window_pos_windows_falls_back_when_scale_factor_is_invalid(self) -> None:
        metrics = FakeMetricsProvider(
            screen_size=(1920, 1080),
            virtual_bounds=(100, 50, 1000, 800),
            scale_factor=0.0,
            platform_name="Windows",
        )
        self.assertEqual(center_window_pos(400, 200, metrics=metrics), (400, 350))

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

    def test_windows_centering_applies_scale_factor(self) -> None:
        metrics = FakeMetricsProvider(
            screen_size=(1920, 1080),
            virtual_bounds=(0, 0, 1920, 1080),
            scale_factor=1.5,
            platform_name="Windows",
        )
        self.assertEqual(center_window_pos(900, 600, metrics=metrics), (340, 160))

    def test_parse_window_size_clamps_windows_size_to_screen(self) -> None:
        metrics = FakeMetricsProvider(
            screen_size=(800, 600),
            virtual_bounds=(0, 0, 800, 600),
            platform_name="Windows",
        )
        self.assertEqual(parse_window_size("2000x2000", 980, 620, metrics=metrics), (720, 480))

    def test_resolve_native_scale_factor_defaults_when_invalid(self) -> None:
        native_window = type("NativeWindow", (), {"scale_factor": 0})()
        self.assertEqual(resolve_native_scale_factor(native_window), 1.0)

    def test_normalize_scale_factor_defaults_when_invalid(self) -> None:
        self.assertEqual(normalize_scale_factor(0), 1.0)
        self.assertEqual(normalize_scale_factor(None), 1.0)

    def test_logical_to_native_size_applies_scale_factor(self) -> None:
        self.assertEqual(logical_to_native_size(158, 172, scale_factor=2.25), (356, 387))

    def test_native_to_logical_size_applies_scale_factor(self) -> None:
        self.assertEqual(native_to_logical_size(2277, 1217, scale_factor=2.25), (1012, 541))

    def test_physical_to_logical_point_applies_scale_factor(self) -> None:
        self.assertEqual(physical_to_logical_point(1530, 900, scale_factor=2.25), (680, 400))

    def test_extract_native_window_geometry_returns_logical_and_raw_sizes(self) -> None:
        native_window = type(
            "NativeWindow",
            (),
            {
                "scale_factor": 2.0,
                "ClientSize": type("ClientSize", (), {"Width": 1800, "Height": 1240})(),
            },
        )()

        geometry = extract_native_window_geometry(native_window)

        self.assertEqual((geometry.width, geometry.height), (900, 620))
        self.assertEqual((geometry.raw_width, geometry.raw_height), (1800, 1240))
        self.assertEqual(geometry.scale_factor, 2.0)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
