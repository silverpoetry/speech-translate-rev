from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.window_geometry import (
    center_window_pos,
    ensure_visible_or_center,
    parse_window_size,
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

    def test_ensure_visible_or_center_recenters_offscreen_window(self) -> None:
        metrics = FakeMetricsProvider(virtual_bounds=(0, 0, 1280, 720))
        self.assertEqual(ensure_visible_or_center(2000, 2000, 400, 300, metrics=metrics), (440, 210))

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


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
