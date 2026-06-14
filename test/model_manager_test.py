from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.model_manager import ModelManagerController


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {"model_f_import": "small", "dir_model": "auto"}
        self.saved = {}

    def save_key(self, key: str, value):
        self.saved[key] = value
        self.cache[key] = value


class FakeBridge:
    def __init__(self) -> None:
        self.messages = []
        self.progress = []

    def reset_task_state(self, title: str):
        self.messages.append(("reset", title))

    def update_task_message(self, message: str, source: str = "general"):
        self.messages.append((source, message))

    def update_task_progress(self, value: float, source: str = "general"):
        self.progress.append((source, value))

    def update_task_error(self, error: str):
        self.messages.append(("error", error))

    def finish_task(self, message: str):
        self.messages.append(("finish", message))

    def get_settings_snapshot(self):
        return {
            "transcribe_mw": True,
            "translate_mw": True,
            "tl_engine_mw": "Google Translate",
            "model_mw": "small",
            "model_f_import": "small",
        }


class ModelManagerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = FakeSettings()
        self.bridge = FakeBridge()
        self.controller = ModelManagerController(self.bridge, self.settings, lambda: None)

    def test_normalize_model_key_maps_display_name(self) -> None:
        self.assertEqual(self.controller.normalize_model_key("⚡ Tiny [1GB VRAM] (Fastest)"), "tiny")
        self.assertEqual(self.controller.normalize_model_key("small"), "small")

    def test_handle_task_message_updates_runtime_state(self) -> None:
        self.controller.handle_task_message("Loading model cache for small")
        self.assertTrue(self.controller.model_load_running)
        self.assertFalse(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_key, "small")

        self.controller.handle_task_message("Model ready: small")
        self.assertFalse(self.controller.model_load_running)
        self.assertTrue(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model ready: small")

    def test_handle_recording_status_marks_runtime_ready(self) -> None:
        self.controller.runtime_model_key = "medium"
        self.controller.handle_recording_status({"status": "Recording..."})
        self.assertFalse(self.controller.model_load_running)
        self.assertTrue(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model ready: medium")

    def test_build_runtime_model_state_reflects_loading_flag(self) -> None:
        self.controller.runtime_model_key = "large-v3"
        self.controller.model_load_running = True
        self.controller.runtime_model_loaded = False
        state = self.controller.build_runtime_model_state()
        self.assertEqual(
            state,
            {
                "key": "large-v3",
                "loading": True,
                "loaded": False,
                "message": self.controller.runtime_model_message,
            },
        )

    def test_mark_runtime_model_pending_sets_loading_state(self) -> None:
        self.controller.mark_runtime_model_pending("medium")
        self.assertEqual(self.controller.runtime_model_key, "medium")
        self.assertTrue(self.controller.model_load_running)
        self.assertFalse(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Loading model cache for medium")

    def test_mark_runtime_model_ready_sets_ready_state(self) -> None:
        self.controller.mark_runtime_model_ready("large-v3")
        self.assertEqual(self.controller.runtime_model_key, "large-v3")
        self.assertFalse(self.controller.model_load_running)
        self.assertTrue(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model ready: large-v3")

    def test_mark_runtime_model_failed_sets_failure_state(self) -> None:
        self.controller.model_load_running = True
        self.controller.runtime_model_loaded = True
        self.controller.mark_runtime_model_failed("Model load failed: boom")
        self.assertFalse(self.controller.model_load_running)
        self.assertFalse(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model load failed: boom")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
