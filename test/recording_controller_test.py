from __future__ import annotations

import os
import sys
import unittest
from threading import Lock

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.recording_controller import RecordingSessionController
from speech_translate.ui_protocol import UI_SECTION_TASK


class FakeModelManager:
    def __init__(self) -> None:
        self.payloads = []
        self.pending_calls = []
        self.ready_calls = []

    def handle_recording_status(self, payload):
        self.payloads.append(payload)

    def mark_runtime_model_pending(self, model_key: str, loaded: bool = False, message=None):
        self.pending_calls.append((model_key, loaded, message))

    def mark_runtime_model_ready(self, model_key: str | None = None, message=None):
        self.ready_calls.append((model_key, message))


class FakeBridge:
    def __init__(self) -> None:
        self._lock = Lock()
        self.model_manager_controller = FakeModelManager()
        self.emits = []
        self._runtime_model_key = "small"
        self._runtime_model_loaded = False
        self._runtime_model_message = ""
        self._model_load_running = False
        self.bound_headless = 0
        self.clear_live_calls = 0
        self.reset_task_titles = []
        self.finished = []
        self.errors = []

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

    def bind_headless_main_window(self) -> None:
        self.bound_headless += 1

    def clear_live(self) -> None:
        self.clear_live_calls += 1

    def reset_task_state(self, title: str) -> None:
        self.reset_task_titles.append(title)

    def finish_task(self, message: str) -> None:
        self.finished.append(message)

    def update_task_error(self, message: str) -> None:
        self.errors.append(message)


class FakeWhisperLoadApi:
    def __init__(self, *, cached_bundle: bool) -> None:
        self.cached_bundle = cached_bundle
        self.calls = []

    def is_model_bundle_cached(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.cached_bundle

    def get_model_args(self, settings_snapshot):
        return {"device": "cpu"}


class RecordingSessionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.shutdown_calls = 0
        self.whisper_api = FakeWhisperLoadApi(cached_bundle=False)
        self.controller = RecordingSessionController(
            self.bridge,
            lambda: self.whisper_api,
            self._shutdown_selenium,
        )

        from speech_translate.linker import bc

        self.bc = bc
        self.previous_recording = self.bc.recording
        self.previous_stream = self.bc.stream
        self.previous_tc_sentences = list(self.bc.tc_sentences)
        self.previous_tl_sentences = list(self.bc.tl_sentences)
        self.bc.recording = False
        self.bc.stream = None
        self.bc.tc_sentences = []
        self.bc.tl_sentences = []

    def tearDown(self) -> None:
        self.bc.recording = self.previous_recording
        self.bc.stream = self.previous_stream
        self.bc.tc_sentences = self.previous_tc_sentences
        self.bc.tl_sentences = self.previous_tl_sentences

    def _shutdown_selenium(self) -> None:
        self.shutdown_calls += 1

    def test_set_recording_state_updates_payload_and_emits(self) -> None:
        result = self.controller.set_recording_state({"status": "Initializing recording...", "active": True})
        self.assertTrue(result["ok"])
        self.assertEqual(self.controller.recording_state["status"], "Initializing recording...")
        self.assertEqual(self.bridge.model_manager_controller.payloads[-1]["status"], "Initializing recording...")
        self.assertEqual(self.bridge.emits[-1], (UI_SECTION_TASK,))

    def test_get_recording_state_returns_copy(self) -> None:
        self.controller.recording_state["status"] = "Stopped"
        state = self.controller.get_recording_state()
        self.assertEqual(state["status"], "Stopped")
        state["status"] = "Changed"
        self.assertEqual(self.controller.recording_state["status"], "Stopped")

    def test_set_recording_state_routes_runtime_status_updates(self) -> None:
        self.controller.set_recording_state({"status": "Recording...", "active": True})
        self.assertEqual(self.bridge.model_manager_controller.payloads[-1]["status"], "Recording...")

    def test_start_recording_rejects_when_all_record_actions_disabled(self) -> None:
        previous_get_settings = self.bridge.get_settings_snapshot

        def disabled_settings():
            return {
                **previous_get_settings(),
                "transcribe_mw": False,
                "translate_mw": False,
            }

        self.bridge.get_settings_snapshot = disabled_settings
        result = self.controller.start_recording()
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "Please enable Transcribe or Translate")
        self.assertEqual(self.bridge.model_manager_controller.pending_calls, [])

    def test_start_recording_marks_cached_bundle_ready_and_updates_state(self) -> None:
        previous_start_worker = self.controller._start_recording_worker
        self.whisper_api.cached_bundle = True
        started_contexts = []
        try:
            self.controller._start_recording_worker = lambda context: started_contexts.append(context)
            result = self.controller.start_recording()
        finally:
            self.controller._start_recording_worker = previous_start_worker

        self.assertTrue(result["ok"])
        self.assertEqual(self.bridge.model_manager_controller.pending_calls[-1][0], "small")
        self.assertEqual(self.bridge.model_manager_controller.ready_calls[-1][0], "small")
        self.assertEqual(self.bridge.reset_task_titles, ["Recording"])
        self.assertEqual(self.bridge.bound_headless, 1)
        self.assertEqual(self.bridge.clear_live_calls, 1)
        self.assertEqual(self.controller.recording_state["status"], "Preparing recording...")
        self.assertEqual(self.controller.recording_state["mode"], "Transcribe & Translate")
        self.assertEqual(len(started_contexts), 1)

    def test_stop_recording_stops_and_closes_selenium_when_idle(self) -> None:
        previous_wait_idle = self.controller.wait_recording_idle
        previous_recording = self.bc.recording
        try:
            self.bc.recording = True
            self.controller.wait_recording_idle = lambda timeout_s=12.0: True
            result = self.controller.stop_recording()
        finally:
            self.controller.wait_recording_idle = previous_wait_idle
            self.bc.recording = previous_recording

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "Recording stopped")
        self.assertEqual(self.shutdown_calls, 0)
        self.assertEqual(self.controller.recording_state["status"], "Stopped")

    def test_stop_recording_closes_selenium_for_selenium_engine(self) -> None:
        previous_wait_idle = self.controller.wait_recording_idle
        previous_recording = self.bc.recording
        previous_get_settings = self.bridge.get_settings_snapshot
        try:
            self.bc.recording = True
            self.controller.wait_recording_idle = lambda timeout_s=12.0: True
            self.bridge.get_settings_snapshot = lambda: {
                **previous_get_settings(),
                "tl_engine_mw": "Selenium Chrome Translate",
                "translate_mw": True,
            }
            result = self.controller.stop_recording()
        finally:
            self.controller.wait_recording_idle = previous_wait_idle
            self.bc.recording = previous_recording
            self.bridge.get_settings_snapshot = previous_get_settings

        self.assertTrue(result["ok"])
        self.assertEqual(self.shutdown_calls, 1)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
