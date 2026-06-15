from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.ui_protocol import TASK_SOURCE_GENERAL, UI_SECTION_TASK
from speech_translate.web_backend import HeadlessQueueWindow, WebTaskBridge


class FakeWindow:
    def __init__(self) -> None:
        self.scripts = []

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


class WebTaskBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = WebTaskBridge()
        self.window = FakeWindow()
        self.bridge.bind_window(self.window)

    def test_update_task_message_tracks_source_in_snapshot(self) -> None:
        self.bridge.reset_task_state("Model Load")
        self.bridge.update_task_message("Loading model cache", source="model-load")
        state = self.bridge.snapshot_task_state()
        self.assertEqual(state["message"], "Loading model cache")
        self.assertEqual(state["message_source"], "model-load")

    def test_update_task_progress_is_monotonic_for_file_import(self) -> None:
        self.bridge.reset_task_state("File Import")
        self.bridge.update_task_progress(60, source="import")
        self.bridge.update_task_progress(20, source="import")
        state = self.bridge.snapshot_task_state()
        self.assertEqual(state["progress"], 60.0)
        self.assertEqual(state["progress_source"], "import")

    def test_update_task_rows_normalizes_nested_sequences(self) -> None:
        self.bridge.update_task_rows((("a.wav", "Waiting"), ("b.wav", "Done")))
        state = self.bridge.snapshot_task_state()
        self.assertEqual(state["rows"], [["a.wav", "Waiting"], ["b.wav", "Done"]])

    def test_set_task_active_updates_snapshot_and_emits(self) -> None:
        self.bridge.set_task_active(True)
        state = self.bridge.snapshot_task_state()
        self.assertTrue(state["active"])
        self.assertTrue(any(UI_SECTION_TASK in script for script in self.window.scripts))

    def test_headless_queue_window_routes_rows_to_bridge(self) -> None:
        queue_window = HeadlessQueueWindow(self.bridge)
        queue_window.update_sheet([["a.wav", "Queued"]])
        self.assertEqual(self.bridge.snapshot_task_state()["rows"], [["a.wav", "Queued"]])

    def test_reset_task_state_emits_task_section(self) -> None:
        self.bridge.reset_task_state("Recording")
        self.assertTrue(any(UI_SECTION_TASK in script for script in self.window.scripts))

    def test_update_live_html_syncs_html_and_text(self) -> None:
        self.bridge.update_live_html("main_transcribed_html", "<span>Hello</span><br />World")
        state = self.bridge.snapshot_live_state()
        self.assertEqual(state["main_transcribed_html"], "<span>Hello</span><br />World")
        self.assertEqual(state["main_transcribed_text"], "Hello\nWorld")

    def test_append_live_text_escapes_html_sensitive_chars(self) -> None:
        self.bridge.append_live_text("main_transcribed", "A<B&>", separator="\n")
        state = self.bridge.snapshot_live_state()
        self.assertEqual(state["main_transcribed_text"], "A<B&>\n")
        self.assertIn("&lt;B&amp;&gt;", state["main_transcribed_html"])


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
