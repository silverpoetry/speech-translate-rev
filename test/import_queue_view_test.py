from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.import_queue_view import (
    build_file_processing_state_payload,
    build_import_batch_ready_message,
    build_import_status_message,
    build_import_ui_payload,
    build_task_progress,
    build_task_rows,
)


class FakeModelManager:
    def normalize_engine_name(self, value: str) -> str:
        return value

    def normalize_model_key(self, value: str) -> str:
        if "Small" in value or value == "small":
            return "small"
        return value

    def resolve_model_dir(self) -> str:
        return "D:\\model-cache"

    def is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        _ = backend, model_dir
        return model_key == "small"


class ImportQueueViewTests(unittest.TestCase):
    def test_build_import_ui_payload_filters_to_available_models(self) -> None:
        payload = build_import_ui_payload(
            {
                "tl_engine_f_import": "Google Translate",
                "model_f_import": "tiny",
                "use_faster_whisper": True,
                "source_lang_f_import": "English",
                "target_lang_f_import": "Chinese",
                "transcribe_f_import": True,
                "translate_f_import": False,
            },
            model_manager=FakeModelManager(),
            source_dict_ref={"Google Translate": ["English"]},
            target_dict_ref={"Google Translate": ["Chinese"]},
            verify_available=True,
        )

        self.assertEqual(payload["selected_backend"], "faster-whisper")
        self.assertEqual(payload["selected_model_key"], "small")
        self.assertEqual(len(payload["model_options"]), 1)
        self.assertEqual(payload["selected_model"], "small")
        self.assertEqual(payload["selected_model_label"], "⛵ Small [2GB VRAM] (Moderate)")
        self.assertEqual(payload["model_options"][0]["value"], "small")

    def test_build_import_ui_payload_uses_model_key_consistently_without_availability_scan(self) -> None:
        payload = build_import_ui_payload(
            {
                "tl_engine_f_import": "Google Translate",
                "model_f_import": "small",
                "use_faster_whisper": True,
                "source_lang_f_import": "English",
                "target_lang_f_import": "Chinese",
                "transcribe_f_import": True,
                "translate_f_import": False,
            },
            model_manager=FakeModelManager(),
            source_dict_ref={"Google Translate": ["English"]},
            target_dict_ref={"Google Translate": ["Chinese"]},
            verify_available=False,
        )

        self.assertEqual(payload["selected_model"], "small")
        self.assertEqual(payload["selected_model_key"], "small")
        self.assertEqual(payload["selected_model_label"], "⛵ Small [2GB VRAM] (Moderate)")
        self.assertEqual(payload["model_options"], [{"value": "small", "label": "⛵ Small [2GB VRAM] (Moderate)"}])

    def test_build_file_processing_state_payload_reports_counts(self) -> None:
        payload = build_file_processing_state_payload(
            [
                {"name": "a.wav", "status": "Done", "is_completed": True},
                {"name": "b.wav", "status": "Waiting", "is_completed": False},
            ],
            active=True,
        )

        self.assertEqual(payload["files_total"], 2)
        self.assertEqual(payload["files_completed"], 1)
        self.assertTrue(payload["active"])

    def test_build_import_status_message_includes_elapsed_time(self) -> None:
        message = build_import_status_message(
            [
                {"name": "a.wav", "status": "Done", "is_completed": True},
                {"name": "b.wav", "status": "Working", "is_completed": False},
            ],
            batch_start_time=100.0,
            time_fn=lambda: 161.0,
        )

        self.assertIn("已完成 1/2 个文件", message)
        self.assertIn("00:01:01", message)

    def test_projection_helpers_build_progress_and_rows(self) -> None:
        display_queue = [
            {"name": "a.wav", "status": "Done", "is_completed": True},
            {"name": "b.wav", "status": "Waiting", "is_completed": False},
        ]

        self.assertEqual(build_import_batch_ready_message(prepared_count=2, total_count=4), "已准备好 2 个待处理文件 | 队列共 4 个")
        self.assertEqual(build_task_progress(display_queue), 50.0)
        self.assertEqual(build_task_rows(display_queue), [["a.wav", "Done"], ["b.wav", "Waiting"]])


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
