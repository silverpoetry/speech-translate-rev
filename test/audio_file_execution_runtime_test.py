from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.file_execution_runtime import (
    WorkerFailure,
    execute_monitored_queue_task,
    run_translate_api,
    run_whisper,
)


class FakeQueue:
    def __init__(self) -> None:
        self.value = None

    def put(self, payload) -> None:
        self.value = payload

    def get(self):
        return self.value


class FakeEnvironment:
    def __init__(self, has_ffmpeg: bool) -> None:
        self.has_ffmpeg = has_ffmpeg


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {}


class FakeWord:
    def __init__(self, word: str, end: float = 0.0) -> None:
        self.word = word
        self.end = end


class FakeSegment:
    def __init__(self, text: str, words: list[FakeWord] | None = None) -> None:
        self.text = text
        self.words = words or []


class FakeQuery:
    def __init__(self, segments: list[FakeSegment]) -> None:
        self.segments = segments
        self.language = ""


class AudioFileExecutionRuntimeTests(unittest.TestCase):
    def test_execute_monitored_queue_task_returns_queue_value(self) -> None:
        queue = FakeQueue()
        result = execute_monitored_queue_task(
            lambda value: queue.put(value),
            cancel_check=lambda: True,
            args=("ok",),
            result_queue=queue,
        )

        self.assertEqual(result, "ok")

    def test_run_whisper_maps_missing_ffmpeg_error(self) -> None:
        failure = WorkerFailure()
        queue = FakeQueue()

        def raise_missing_file(_audio, **_kwargs):
            raise RuntimeError("The system cannot find the file specified")

        run_whisper(
            raise_missing_file,
            "a.wav",
            "transcribe",
            failure,
            result_queue=queue,
            environment=FakeEnvironment(has_ffmpeg=False),
        )

        self.assertTrue(failure.failed)
        self.assertEqual(str(failure.error), "FFmpeg not found in system path. Please install FFmpeg.")

    def test_run_translate_api_updates_segment_words(self) -> None:
        query = FakeQuery([FakeSegment("hello world", [FakeWord("hello", 0.5), FakeWord("world", 1.0)])])
        failure = WorkerFailure()

        class RuntimeSettings:
            http_proxy = ""
            https_proxy = ""
            debug_translate = False

        run_translate_api(
            query,
            "Google Translate",
            "English",
            "Chinese",
            failure,
            FakeSettings(),
            translate_fn=lambda *args, **kwargs: (True, ["ni hao"]),
            runtime_settings_factory=lambda cache: RuntimeSettings(),
            proxies_getter=lambda http_proxy, https_proxy: {"http": http_proxy, "https": https_proxy},
        )

        self.assertFalse(failure.failed)
        self.assertEqual(query.language, "Chinese")
        self.assertEqual(query.segments[0].words[0].word, " ni")
        self.assertEqual(query.segments[0].words[1].word, " hao")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
