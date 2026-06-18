from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.file_batch_domain import FileBatchStatusContext
from speech_translate.utils.audio.file_postprocess_workflows import (
    process_mod_batch,
    process_translate_result_batch,
)


class FakeUiBridge:
    def __init__(self) -> None:
        self.batches = []
        self.calls = []

    def init_file_batch(self, task_name: str, files) -> None:
        self.batches.append((task_name, list(files)))

    def sync_file_status(self, index: int, status: str, is_completed: bool) -> None:
        self.calls.append((index, status, is_completed))


class FakeProcessingState:
    def __init__(self) -> None:
        self._next_values = [True, False]
        self.mod_count = 0

    def reset_mod_counter(self) -> None:
        self.mod_count = 0

    def is_file_processing(self) -> bool:
        if self._next_values:
            return self._next_values.pop(0)
        return False

    def increment_mod_counter(self) -> None:
        self.mod_count += 1

    def mod_counter(self) -> int:
        return self.mod_count


class FakeRuntimeSettings:
    export_format = "{file}"
    auto_open_dir_translate = False

    def should_auto_open_dir(self, _mode: str) -> bool:
        return False


class AudioFilePostprocessWorkflowTests(unittest.TestCase):
    def test_process_mod_batch_inits_batch_and_marks_parse_error(self) -> None:
        bridge = FakeUiBridge()
        runtime = type(
            "Runtime",
            (),
            {
                "status_context": FileBatchStatusContext(is_mod=True, ui_bridge=bridge),
                "processing_state": FakeProcessingState(),
                "ui_bridge": bridge,
                "slice_start": None,
                "slice_end": None,
                "runtime_settings": FakeRuntimeSettings(),
                "export_dir": "D:\\exports",
                "action": "Alignment",
                "stable_whisper_api": type("Api", (), {"WhisperResult": lambda self, path: (_ for _ in ()).throw(RuntimeError("boom"))})(),
                "model": object(),
                "mod_func": lambda *args, **kwargs: None,
                "mod_args": {},
                "result_queue": object(),
                "started_at": 0.0,
            },
        )()
        request = type(
            "Request",
            (),
            {"mode": "alignment", "model_name_tc": "medium", "data_files": [("a.wav", "bad.json")]},
        )()

        process_mod_batch(
            request,
            runtime,
            get_transcribe_args=lambda func, snapshot: {},
            resolve_language_code=lambda language: language,
            sleep_fn=lambda _: None,
            time_fn=lambda: 0.0,
        )

        self.assertEqual(bridge.batches, [("Task alignment with medium", ["a.wav"])])
        self.assertEqual(bridge.calls[-1], (0, "Parse Error", True))

    def test_process_translate_result_batch_inits_batch_and_marks_parse_error(self) -> None:
        bridge = FakeUiBridge()
        runtime = type(
            "Runtime",
            (),
            {
                "status_context": FileBatchStatusContext(is_mod=True, ui_bridge=bridge),
                "processing_state": FakeProcessingState(),
                "ui_bridge": bridge,
                "slice_start": None,
                "slice_end": None,
                "runtime_settings": FakeRuntimeSettings(),
                "export_dir": "D:\\exports",
                "stable_whisper_api": type("Api", (), {"WhisperResult": lambda self, path: (_ for _ in ()).throw(RuntimeError("boom"))})(),
                "api_kwargs": {},
                "settings": object(),
                "started_at": 0.0,
            },
        )()
        request = type(
            "Request",
            (),
            {"engine": "Google Translate", "lang_target": "Chinese", "data_files": ["bad.json"]},
        )()

        process_translate_result_batch(
            request,
            runtime,
            sleep_fn=lambda _: None,
            time_fn=lambda: 0.0,
        )

        self.assertEqual(bridge.batches, [("Task Translate with Google Translate", ["bad.json"])])
        self.assertEqual(bridge.calls[-1], (0, "Parse Error", True))


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
