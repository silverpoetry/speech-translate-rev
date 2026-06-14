from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.recording_controller import RecordingSessionController


class DummyLock:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeModelManager:
    def __init__(self) -> None:
        self.payloads = []

    def handle_recording_status(self, payload):
        self.payloads.append(payload)


class FakeBridge:
    def __init__(self) -> None:
        self._lock = DummyLock()
        self.model_manager_controller = FakeModelManager()
        self.emits = []
        self._runtime_model_key = "small"
        self._runtime_model_loaded = False
        self._runtime_model_message = ""
        self._model_load_running = False

    def _emit_ui_update(self, sections):
        self.emits.append(tuple(sections))

    def get_settings_snapshot(self):
        return {
            "source_lang_mw": "English",
            "target_lang_mw": "Chinese",
            "input": "mic",
            "tl_engine_mw": "Google Translate",
            "transcribe_mw": True,
            "translate_mw": True,
            "model_mw": "small",
            "selenium_auto_close_on_task_done": True,
        }

    def _normalize_engine_name(self, value: str) -> str:
        return value

    def _normalize_model_key(self, value: str) -> str:
        return value


class RecordingSessionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.controller = RecordingSessionController(self.bridge, lambda: None, lambda: None)

    def test_set_recording_state_updates_payload_and_emits(self) -> None:
        result = self.controller.set_recording_state({"status": "Initializing recording...", "active": True})
        self.assertTrue(result["ok"])
        self.assertEqual(self.controller.recording_state["status"], "Initializing recording...")
        self.assertEqual(self.bridge.model_manager_controller.payloads[-1]["status"], "Initializing recording...")
        self.assertEqual(self.bridge.emits[-1], ("task",))

    def test_get_recording_state_returns_copy(self) -> None:
        self.controller.recording_state["status"] = "Stopped"
        state = self.controller.get_recording_state()
        self.assertEqual(state["status"], "Stopped")
        state["status"] = "Changed"
        self.assertEqual(self.controller.recording_state["status"], "Stopped")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
