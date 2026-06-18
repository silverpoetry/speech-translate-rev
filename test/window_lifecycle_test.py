from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.window_geometry import WindowPlacement
from speech_translate.window_lifecycle import (
    attach_preloaded_window,
    build_offscreen_preload_placement,
    consume_pending_preloaded_window,
    get_target_placement,
    is_preloaded_window,
    preload_window_creation,
    reveal_preloaded_window,
    should_skip_preloaded_geometry_save,
)


class FakeWindow:
    def __init__(self) -> None:
        self.native = None
        self.brought = False

    def bring_to_front(self) -> None:
        self.brought = True


class WindowLifecycleTests(unittest.TestCase):
    def test_preload_window_creation_exposes_pending_plan(self) -> None:
        target = WindowPlacement(width=640, height=320, x=120, y=80)

        with preload_window_creation(target) as plan:
            consumed = consume_pending_preloaded_window()

        self.assertIsNotNone(consumed)
        self.assertEqual(plan.target_placement, target)
        self.assertEqual(consumed.target_placement, target)
        self.assertEqual(plan.offscreen_placement.width, 640)
        self.assertEqual(plan.offscreen_placement.height, 320)

    def test_reveal_preloaded_window_restores_target_placement_and_marks_revealed(self) -> None:
        window = FakeWindow()
        plan = type(
            "Plan",
            (),
            {
                "target_placement": WindowPlacement(width=640, height=320, x=120, y=80),
                "offscreen_placement": build_offscreen_preload_placement(640, 320),
            },
        )()
        attach_preloaded_window(window, plan)

        with (
            patch("speech_translate.window_lifecycle.apply_native_window_placement", return_value=True) as apply_placement,
            patch("speech_translate.window_lifecycle.set_native_window_opacity", return_value=True) as set_opacity,
        ):
            result = reveal_preloaded_window(window)

        self.assertTrue(result)
        self.assertTrue(window.brought)
        self.assertEqual(get_target_placement(window), WindowPlacement(width=640, height=320, x=120, y=80))
        self.assertFalse(is_preloaded_window(window))
        self.assertFalse(should_skip_preloaded_geometry_save(window))
        apply_placement.assert_called_once()
        set_opacity.assert_called_once_with(None, 1.0)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
