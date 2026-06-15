from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.import_queue_manager import ImportQueueController
from speech_translate.bridge_runtime_state import BridgeFileRuntime, BridgeRecordingRuntime
from speech_translate.runtime_registry import bridge_state_registry
from speech_translate.ui_protocol import TASK_SOURCE_IMPORT, UI_SECTION_IMPORT


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {
            "tl_engine_f_import": "Google Translate",
            "model_f_import": "small",
            "use_faster_whisper": True,
            "source_lang_f_import": "English",
            "target_lang_f_import": "Chinese",
            "transcribe_f_import": True,
            "translate_f_import": True,
        }


class FakeBridge:
    TL_ENGINE_SOURCE_DICT_REF = {"Google Translate": ["English"]}
    TL_ENGINE_TARGET_DICT_REF = {"Google Translate": ["Chinese"]}

    def __init__(self) -> None:
        self.model_manager_controller = FakeModelManager()
        self.updates = []
        self.window = None
        self.bound_headless = 0
        self.finished = []
        self.errors = []
        self.settings_snapshot = dict(FakeSettings().cache)

    def update_task_message(self, message: str, source: str = "general"):
        self.updates.append(("message", source, message))

    def update_task_progress(self, value: float, source: str = "general"):
        self.updates.append(("progress", source, value))

    def update_task_rows(self, rows):
        self.updates.append(("rows", rows))

    def emit_ui_update(self, sections):
        self.updates.append(("emit", tuple(sections)))

    def get_recording_state(self):
        return {"active": False}

    def wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        return True

    def get_window(self):
        return self.window

    def reset_task_state(self, title: str):
        self.updates.append(("reset", title))

    def set_task_title(self, title: str):
        self.updates.append(("title", title))

    def finish_task(self, message: str):
        self.finished.append(message)

    def update_task_error(self, message: str):
        self.errors.append(message)

    def get_settings_snapshot(self):
        return dict(self.settings_snapshot)


class FakeModelManager:
    def __init__(self) -> None:
        self.pending_calls = []
        self.ready_calls = []
        self.model_load_running = False
        self.runtime_model_loaded = False
        self.runtime_model_key = "small"

    def mark_runtime_model_pending(self, model_key, loaded=False, message=None):
        self.pending_calls.append((model_key, loaded, message))
        self.runtime_model_key = model_key
        self.runtime_model_loaded = bool(loaded)
        self.model_load_running = True

    def mark_runtime_model_ready(self, model_key=None, message=None):
        resolved_key = model_key or self.runtime_model_key
        self.ready_calls.append((resolved_key, message))
        self.runtime_model_key = resolved_key
        self.runtime_model_loaded = True
        self.model_load_running = False

    def normalize_engine_name(self, value: str) -> str:
        return value

    def normalize_model_key(self, value: str) -> str:
        return value

    def resolve_model_dir(self) -> str:
        return "D:\\model-cache"

    def is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        return model_key == "small"


class FakeProcessRuntime:
    def __init__(self) -> None:
        self.recording_active = False
        self.file_processing_active = False
        self.enabled = 0
        self.disabled = 0
        self.tc_count = 0
        self.tl_count = 0

    def is_recording_active(self) -> bool:
        return self.recording_active

    def is_file_processing_active(self) -> bool:
        return self.file_processing_active

    def enable_file_processing(self) -> None:
        self.enabled += 1
        self.file_processing_active = True

    def disable_file_processing(self) -> None:
        self.disabled += 1
        self.file_processing_active = False

    def transcribed_count(self) -> int:
        return self.tc_count

    def translated_count(self) -> int:
        return self.tl_count


class ImportQueueControllerTests(unittest.TestCase):
    def test_import_process_runtime_default_provider_reads_bridge_substates(self) -> None:
        from speech_translate.import_queue_manager import ImportQueueProcessRuntime

        previous_bridge_state = bridge_state_registry.state
        fake_bridge = type(
            "FakeBridgeState",
            (),
            {
                "recording_runtime": BridgeRecordingRuntime(recording=True),
                "file_runtime": BridgeFileRuntime(file_processing=True, file_tced_counter=3, file_tled_counter=4),
            },
        )()
        try:
            bridge_state_registry.set(fake_bridge)

            runtime = ImportQueueProcessRuntime()
            self.assertTrue(runtime.is_recording_active())
            self.assertTrue(runtime.is_file_processing_active())
            self.assertEqual(runtime.transcribed_count(), 3)
            self.assertEqual(runtime.translated_count(), 4)
        finally:
            bridge_state_registry.set(previous_bridge_state)

    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.settings = FakeSettings()
        self.bridge.settings_snapshot = self.settings.cache
        self.process_runtime = FakeProcessRuntime()
        self.controller = ImportQueueController(
            self.bridge,
            self.settings,
            lambda: None,
            self.bridge.model_manager_controller,
            process_runtime=self.process_runtime,
        )

    def test_get_full_display_queue_merges_processing_status(self) -> None:
        self.controller.file_import_queue = [{"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False}]
        self.controller.processing_queue = [{"path": "a.wav", "name": "a.wav", "status": "Transcribing", "is_completed": False}]
        queue = self.controller.get_full_display_queue()
        self.assertEqual(queue[0]["status"], "Transcribing")

    def test_add_files_to_import_queue_deduplicates(self) -> None:
        result = self.controller.add_files_to_import_queue(["a.wav", "a.wav"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(self.controller.file_import_queue), 1)

    def test_add_files_to_import_queue_uses_shared_dialog_runtime_when_files_omitted(self) -> None:
        self.bridge.window = object()
        with patch("speech_translate.import_queue_manager.create_file_dialog", return_value=["dialog.wav"]) as create_dialog:
            result = self.controller.add_files_to_import_queue()

        self.assertTrue(result["ok"])
        self.assertEqual(self.controller.file_import_queue[0]["path"], "dialog.wav")
        create_dialog.assert_called_once()

    def test_clear_import_queue_resets_both_queues(self) -> None:
        self.controller.file_import_queue = ["a.wav"]
        self.controller.processing_queue = [{"path": "a.wav"}]
        result = self.controller.clear_import_queue()
        self.assertTrue(result["ok"])
        self.assertEqual(self.controller.file_import_queue, [])
        self.assertEqual(self.controller.processing_queue, [])
        self.assertIn(("emit", (UI_SECTION_IMPORT,)), self.bridge.updates)

    def test_build_import_ui_uses_available_models(self) -> None:
        payload = self.controller.build_import_ui(verify_available=True)
        self.assertEqual(payload["selected_model"], "")
        self.assertIn("small", payload["selected_model_key"] or "small")

    def test_extract_files_to_process_skips_completed_entries(self) -> None:
        self.controller.file_import_queue = [
            {"path": "a.wav", "name": "a.wav", "status": "Done", "is_completed": True},
            {"path": "b.wav", "name": "b.wav", "status": "Waiting", "is_completed": False},
            "c.wav",
        ]
        self.assertEqual(self.controller._extract_files_to_process(), ["b.wav", "c.wav"])

    def test_normalize_queue_item_supports_str_and_dict(self) -> None:
        item_from_str = self.controller._normalize_queue_item("a.wav")
        item_from_dict = self.controller._normalize_queue_item({"path": "b.wav", "name": "Bee", "status": "Queued", "is_completed": True})
        self.assertEqual(item_from_str.path, "a.wav")
        self.assertEqual(item_from_str.name, "a.wav")
        self.assertEqual(item_from_dict.name, "Bee")
        self.assertTrue(item_from_dict.is_completed)

    def test_sync_file_status_uses_import_source_for_task_message(self) -> None:
        self.controller.file_import_queue = [{"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False}]
        self.controller.processing_queue = [{"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False}]
        self.controller.sync_file_status(0, "Done", True)
        self.assertTrue(any(update[0] == "message" and update[1] == TASK_SOURCE_IMPORT for update in self.bridge.updates))

    def test_start_import_queue_uses_engine_name_and_prepares_runtime_model(self) -> None:
        previous_runtime_loaded = self.bridge.model_manager_controller.runtime_model_loaded
        previous_runtime_key = self.bridge.model_manager_controller.runtime_model_key
        previous_model_load_running = self.bridge.model_manager_controller.model_load_running
        previous_start_worker = self.controller._start_import_worker
        captured = {}
        try:
            self.bridge.model_manager_controller.runtime_model_loaded = True
            self.bridge.model_manager_controller.runtime_model_key = "small"
            self.bridge.model_manager_controller.model_load_running = False
            self.controller.file_import_queue = [{"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False}]
            self.controller._start_import_worker = lambda *, context: captured.update(
                {"engine": context.engine, "model": context.model_name_tc, "prepare": context.should_prepare_runtime_model}
            )
            self.settings.cache["tl_engine_f_import"] = "Selenium Chrome Translate"
            self.settings.cache["model_f_import"] = "small"
            result = self.controller.start_import_queue()
        finally:
            self.bridge.model_manager_controller.runtime_model_loaded = previous_runtime_loaded
            self.bridge.model_manager_controller.runtime_model_key = previous_runtime_key
            self.bridge.model_manager_controller.model_load_running = previous_model_load_running
            self.controller._start_import_worker = previous_start_worker

        self.assertTrue(result["ok"])
        self.assertEqual(captured["engine"], "Selenium Chrome Translate")
        self.assertEqual(captured["model"], "small")
        self.assertTrue(captured["prepare"])
        self.assertEqual(self.bridge.model_manager_controller.ready_calls[-1][0], "small")

    def test_start_import_queue_closes_selenium_when_configured(self) -> None:
        previous_shutdown = self.controller.shutdown_selenium_fn
        previous_start_worker = self.controller._start_import_worker
        shutdown_calls = []
        try:
            self.controller.shutdown_selenium_fn = lambda: shutdown_calls.append("shutdown")
            self.controller.file_import_queue = [{"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False}]

            def fake_start_worker(*, context):
                self.controller._finish_import_run(context=context)

            self.controller._start_import_worker = fake_start_worker
            self.settings.cache["tl_engine_f_import"] = "Selenium Chrome Translate"
            self.settings.cache["selenium_auto_close_on_task_done"] = True
            result = self.controller.start_import_queue()
        finally:
            self.controller.shutdown_selenium_fn = previous_shutdown
            self.controller._start_import_worker = previous_start_worker

        self.assertTrue(result["ok"])
        self.assertEqual(shutdown_calls, ["shutdown"])

    def test_get_file_processing_state_uses_injected_process_runtime(self) -> None:
        self.controller.processing_queue = [{"path": "a.wav", "name": "a.wav", "status": "Working", "is_completed": False}]
        self.controller.file_import_queue = [{"path": "a.wav", "name": "a.wav", "status": "Working", "is_completed": False}]
        self.process_runtime.file_processing_active = True

        state = self.controller.get_file_processing_state()

        self.assertTrue(state["active"])

    def test_finish_import_run_clears_runtime_loading_flag_via_model_manager(self) -> None:
        self.bridge.model_manager_controller.model_load_running = True
        self.controller.processing_queue = [{"path": "a.wav", "name": "a.wav", "status": "Done", "is_completed": True}]
        self.controller.file_import_queue = [{"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False}]

        self.controller._finish_import_run(
            context=self.controller._build_import_start_context(),
        )

        self.assertFalse(self.bridge.model_manager_controller.model_load_running)
        self.assertEqual(self.process_runtime.disabled, 1)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
