from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from speech_translate.window_geometry import WindowPlacement

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.window_factory import create_preloaded_window


class FakeWebview:
    def __init__(self) -> None:
        self.calls = []

    def create_window(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


class WindowFactoryTests(unittest.TestCase):
    def test_create_preloaded_window_uses_offscreen_coordinates(self) -> None:
        fake_webview = FakeWebview()
        placement = WindowPlacement(width=640, height=360, x=120, y=80)

        @contextmanager
        def fake_preload_window_creation(_placement):
            yield type(
                "Plan",
                (),
                {"offscreen_placement": WindowPlacement(width=640, height=360, x=2600, y=140)},
            )()

        with patch("speech_translate.window_factory.preload_window_creation", fake_preload_window_creation):
            create_preloaded_window(
                fake_webview,
                "Title",
                "page.html",
                placement=placement,
                background_color="#fff",
            )

        _args, kwargs = fake_webview.calls[0]
        self.assertEqual((kwargs["width"], kwargs["height"]), (640, 360))
        self.assertEqual((kwargs["x"], kwargs["y"]), (2600, 140))

    def test_create_preloaded_window_clears_contract_after_create(self) -> None:
        fake_webview = FakeWebview()
        placement = WindowPlacement(width=400, height=240, x=20, y=30)

        @contextmanager
        def fake_preload_window_creation(_placement):
            yield type(
                "Plan",
                (),
                {"offscreen_placement": WindowPlacement(width=400, height=240, x=2800, y=200)},
            )()

        with (
            patch("speech_translate.window_factory.preload_window_creation", fake_preload_window_creation),
            patch("speech_translate.window_factory.set_pending_window_contract") as set_contract,
        ):
            create_preloaded_window(
                fake_webview,
                "Title",
                "page.html",
                placement=placement,
                native_contract={"kind": "detached_window"},
            )

        self.assertEqual(set_contract.call_args_list[0].args[0], {"kind": "detached_window"})
        self.assertIsNone(set_contract.call_args_list[-1].args[0])

    def test_create_preloaded_window_rejects_reserved_geometry_overrides(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved overrides"):
            create_preloaded_window(
                FakeWebview(),
                "Title",
                "page.html",
                placement=WindowPlacement(width=400, height=240, x=20, y=30),
                width=999,
            )


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
