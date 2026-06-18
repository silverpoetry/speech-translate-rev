from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.file_workflows import process_file_batch


class FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=True):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False
        self.joined = False

    def start(self):
        self.started = True

    def join(self):
        self.joined = True


class FakeProcessingState:
    def __init__(self) -> None:
        self.file_processing = True
        self.tc_enabled = 0
        self.tl_enabled = 0
        self.tc_count = 0
        self.tl_count = 0

    def reset_file_counts(self) -> None:
        self.tc_count = 0
        self.tl_count = 0

    def enable_file_tc(self) -> None:
        self.tc_enabled += 1

    def enable_file_tl(self) -> None:
        self.tl_enabled += 1

    def is_file_processing(self) -> bool:
        active = self.file_processing
        self.file_processing = False
        return active

    def transcribed_count(self) -> int:
        return self.tc_count

    def translated_count(self) -> int:
        return self.tl_count


class FakeStatusContext:
    def has_active_work(self, item_count: int) -> bool:
        return False


class FakeUiBridge:
    def __init__(self) -> None:
        self.batches = []

    def init_file_batch(self, task_name: str, files) -> None:
        self.batches.append((task_name, list(files)))


class FakeRuntimeSettings:
    export_format = "{file}"
    auto_open_dir_export = False


class AudioFileWorkflowTests(unittest.TestCase):
    def test_process_file_batch_routes_translate_only_whisper_to_translate_target(self) -> None:
        thread_calls = []

        def fake_thread_factory(*args, **kwargs):
            thread = FakeThread(*args, **kwargs)
            thread_calls.append(thread)
            return thread

        request = type(
            "Request",
            (),
            {
                "data_files": ["a.wav"],
                "lang_source": "English",
                "lang_target": "Chinese",
                "model_name_tc": "small",
                "engine": "small",
                "is_tc": False,
                "is_tl": True,
            },
        )()
        runtime = type(
            "Runtime",
            (),
            {
                "status_context": FakeStatusContext(),
                "processing_state": FakeProcessingState(),
                "ui_bridge": FakeUiBridge(),
                "taskname": "Translate",
                "slice_start": None,
                "slice_end": None,
                "runtime_settings": FakeRuntimeSettings(),
                "export_dir": "D:\\exports",
                "tl_engine_whisper": True,
                "stable_tl": object(),
                "stable_tc": object(),
                "filters": {},
                "whisper_args": {},
                "result_queue": object(),
                "settings": object(),
                "environment": object(),
                "started_at": 0.0,
            },
        )()

        process_file_batch(
            request,
            runtime,
            translate_target_fn=lambda *args, **kwargs: None,
            transcribe_target_fn=lambda *args, **kwargs: None,
            thread_factory=fake_thread_factory,
            sleep_fn=lambda _: None,
            time_fn=lambda: 0.0,
        )

        self.assertEqual(runtime.ui_bridge.batches, [("Task: Translate with small", ["a.wav"])])
        self.assertEqual(len(thread_calls), 1)
        self.assertTrue(thread_calls[0].started)
        self.assertFalse(thread_calls[0].joined)

    def test_process_file_batch_routes_transcribe_flow_through_joined_thread(self) -> None:
        thread_calls = []

        def fake_thread_factory(*args, **kwargs):
            thread = FakeThread(*args, **kwargs)
            thread_calls.append(thread)
            return thread

        request = type(
            "Request",
            (),
            {
                "data_files": ["a.wav"],
                "lang_source": "English",
                "lang_target": "Chinese",
                "model_name_tc": "small",
                "engine": "Google Translate",
                "is_tc": True,
                "is_tl": False,
            },
        )()
        runtime = type(
            "Runtime",
            (),
            {
                "status_context": FakeStatusContext(),
                "processing_state": FakeProcessingState(),
                "ui_bridge": FakeUiBridge(),
                "taskname": "Transcribe",
                "slice_start": None,
                "slice_end": None,
                "runtime_settings": FakeRuntimeSettings(),
                "export_dir": "D:\\exports",
                "tl_engine_whisper": False,
                "stable_tl": object(),
                "stable_tc": object(),
                "filters": {},
                "whisper_args": {},
                "result_queue": object(),
                "settings": object(),
                "environment": object(),
                "started_at": 0.0,
            },
        )()

        process_file_batch(
            request,
            runtime,
            translate_target_fn=lambda *args, **kwargs: None,
            transcribe_target_fn=lambda *args, **kwargs: None,
            thread_factory=fake_thread_factory,
            sleep_fn=lambda _: None,
            time_fn=lambda: 0.0,
        )

        self.assertEqual(len(thread_calls), 1)
        self.assertTrue(thread_calls[0].started)
        self.assertTrue(thread_calls[0].joined)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
