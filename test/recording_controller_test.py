from __future__ import annotations

import os
import sys
import unittest

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
        self.model_manager_controller = FakeModelManager()
        self.emits = []
        self.bound_headless = 0
        self.clear_live_calls = 0
        self.reset_task_titles = []
        self.finished = []
        self.errors = []

    def emit_ui_update(self, sections):
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

    def normalize_engine_name(self, value: str) -> str:
        return value

    def normalize_model_key(self, value: str) -> str:
        return value

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


class FakeRecordingRuntimeState:
    def __init__(self) -> None:
        self.recording = False
        self.stream = None
        self.enabled = 0
        self.disabled = 0

    def is_recording_active(self) -> bool:
        return self.recording

    def enable_recording(self) -> None:
        self.enabled += 1
        self.recording = True

    def disable_recording(self) -> None:
        self.disabled += 1
        self.recording = False

    def is_stream_released(self) -> bool:
        return self.stream is None


class FakeRecordingTextStore:
    def __init__(self) -> None:
        self.tc_sentences = []
        self.tl_sentences = []

    def set_transcribed_sentences(self, sentences) -> None:
        self.tc_sentences = list(sentences)

    def set_translated_sentences(self, sentences) -> None:
        self.tl_sentences = list(sentences)


class RecordingSessionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.shutdown_calls = 0
        self.whisper_api = FakeWhisperLoadApi(cached_bundle=False)
        self.runtime_state = FakeRecordingRuntimeState()
        self.text_store = FakeRecordingTextStore()
        self.controller = RecordingSessionController(
            self.bridge,
            lambda: self.whisper_api,
            self._shutdown_selenium,
            runtime_state=self.runtime_state,
            text_store=self.text_store,
        )

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
        self.assertEqual(self.bridge.clear_live_calls, 1)
        self.assertEqual(self.runtime_state.enabled, 1)
        self.assertEqual(self.text_store.tc_sentences, [])
        self.assertEqual(self.text_store.tl_sentences, [])
        self.assertEqual(self.controller.recording_state["status"], "Preparing recording...")
        self.assertEqual(self.controller.recording_state["mode"], "Transcribe & Translate")
        self.assertEqual(len(started_contexts), 1)

    def test_start_recording_worker_passes_explicit_session_dependencies(self) -> None:
        from speech_translate import recording_controller as controller_module
        from speech_translate.utils.audio import record as record_module

        previous_thread = controller_module.Thread
        previous_record_session = record_module.record_session
        observed = {}

        class InlineThread:
            def __init__(self, target, daemon=None) -> None:
                self._target = target
                self._alive = False

            def start(self) -> None:
                self._alive = True
                try:
                    self._target()
                finally:
                    self._alive = False

            def is_alive(self) -> bool:
                return self._alive

        def fake_record_session(*args, **kwargs) -> None:
            observed["args"] = args
            observed["kwargs"] = kwargs

        try:
            controller_module.Thread = InlineThread
            record_module.record_session = fake_record_session
            context = self.controller._resolve_start_context(
                device="mic",
                lang_source="English",
                lang_target="Chinese",
                engine="Google Translate",
                is_tc=True,
                is_tl=True,
            )
            self.controller._start_recording_worker(context)
        finally:
            controller_module.Thread = previous_thread
            record_module.record_session = previous_record_session

        self.assertEqual(observed["args"][:4], ("English", "Chinese", "Google Translate", "small"))
        self.assertEqual(observed["kwargs"]["settings_snapshot"], context.settings_snapshot)
        self.assertIs(observed["kwargs"]["session_control"].runtime_state, self.runtime_state)
        self.assertIs(observed["kwargs"]["runtime_text_state"]._text_store, self.text_store)
        self.assertIsNotNone(observed["kwargs"]["callback_context_store"])
        self.assertEqual(self.bridge.finished, ["Recording finished"])

    def test_stop_recording_stops_and_closes_selenium_when_idle(self) -> None:
        previous_wait_idle = self.controller.wait_recording_idle
        try:
            self.runtime_state.recording = True
            self.controller.wait_recording_idle = lambda timeout_s=12.0: True
            result = self.controller.stop_recording()
        finally:
            self.controller.wait_recording_idle = previous_wait_idle

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "Recording stopped")
        self.assertEqual(self.shutdown_calls, 0)
        self.assertGreaterEqual(self.runtime_state.disabled, 1)
        self.assertEqual(self.controller.recording_state["status"], "Stopped")

    def test_stop_recording_closes_selenium_for_selenium_engine(self) -> None:
        previous_wait_idle = self.controller.wait_recording_idle
        previous_get_settings = self.bridge.get_settings_snapshot
        try:
            self.runtime_state.recording = True
            self.controller.wait_recording_idle = lambda timeout_s=12.0: True
            self.bridge.get_settings_snapshot = lambda: {
                **previous_get_settings(),
                "tl_engine_mw": "Selenium Chrome Translate",
                "translate_mw": True,
            }
            result = self.controller.stop_recording()
        finally:
            self.controller.wait_recording_idle = previous_wait_idle
            self.bridge.get_settings_snapshot = previous_get_settings

        self.assertTrue(result["ok"])
        self.assertEqual(self.shutdown_calls, 1)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
