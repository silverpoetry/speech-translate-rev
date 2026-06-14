from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.import_queue_manager import ImportQueueController
from speech_translate.ui_protocol import TASK_SOURCE_IMPORT, UI_SECTION_IMPORT


class DummyLock:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


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
        self._lock = DummyLock()
        self._model_load_running = False
        self._runtime_model_loaded = False
        self._runtime_model_key = "small"
        self._runtime_model_message = ""
        self.task_state = type("TaskState", (), {"title": ""})()
        self.updates = []
        self.window = None

    def _normalize_engine_name(self, value: str) -> str:
        return value

    def _normalize_model_key(self, value: str) -> str:
        return value

    def _resolve_model_dir(self) -> str:
        return "D:\\model-cache"

    def _is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        return model_key == "small"

    def update_task_message(self, message: str, source: str = "general"):
        self.updates.append(("message", source, message))

    def update_task_progress(self, value: float, source: str = "general"):
        self.updates.append(("progress", source, value))

    def update_task_rows(self, rows):
        self.updates.append(("rows", rows))

    def _emit_ui_update(self, sections):
        self.updates.append(("emit", tuple(sections)))

    def get_recording_state(self):
        return {"active": False}

    def _wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        return True

    def get_window(self):
        return self.window

    def reset_task_state(self, title: str):
        self.task_state.title = title

    def bind_headless_main_window(self):
        return None

    def finish_task(self, message: str):
        self.updates.append(("finish", message))

    def update_task_error(self, message: str):
        self.updates.append(("error", message))

    def get_settings_snapshot(self):
        return dict(FakeSettings().cache)


class ImportQueueControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.settings = FakeSettings()
        self.controller = ImportQueueController(self.bridge, self.settings, object, lambda *args, **kwargs: True, lambda: None)

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


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
