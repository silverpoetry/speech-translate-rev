from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.webview_runtime import create_file_dialog, resolve_file_dialog


class FakeWindow:
    def __init__(self) -> None:
        self.calls = []

    def create_file_dialog(self, dialog, **kwargs):
        self.calls.append((dialog, kwargs))
        return ["picked"]


class WebviewRuntimeTests(unittest.TestCase):
    def test_resolve_file_dialog_uses_pywebview_v5_enums(self) -> None:
        modern = type("ModernWebview", (), {"FileDialog": type("FD", (), {"OPEN": "modern-open", "FOLDER": "modern-folder"})})()

        self.assertEqual(resolve_file_dialog(modern, "open"), "modern-open")
        self.assertEqual(resolve_file_dialog(modern, "folder"), "modern-folder")

    def test_resolve_file_dialog_rejects_legacy_webview_contract(self) -> None:
        legacy = type("LegacyWebview", (), {"OPEN_DIALOG": "legacy-open", "FOLDER_DIALOG": "legacy-folder"})()

        with self.assertRaisesRegex(RuntimeError, "pywebview FileDialog API is unavailable"):
            resolve_file_dialog(legacy, "open")

    def test_create_file_dialog_passes_only_relevant_kwargs(self) -> None:
        window = FakeWindow()
        fake_webview = type("FakeWebview", (), {"FileDialog": type("FD", (), {"OPEN": "open"})})()
        with patch("speech_translate.webview_runtime.load_webview_runtime", return_value=fake_webview):
            result = create_file_dialog(
                window,
                dialog_kind="open",
                allow_multiple=True,
                file_types=["*.wav"],
            )

        self.assertEqual(result, ["picked"])
        self.assertEqual(window.calls, [("open", {"allow_multiple": True, "file_types": ["*.wav"]})])


if __name__ == "__main__":
    unittest.main()
