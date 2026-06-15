from __future__ import annotations

import importlib
import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)


class FakeLoguruLogger:
    def __init__(self) -> None:
        self._core = object()
        self.removed = []
        self.add_calls = []

    def add(self, sink, **kwargs):
        self.add_calls.append((sink, kwargs))
        return len(self.add_calls)

    def remove(self, handler_id=None):
        self.removed.append(handler_id)

    def error(self, _message):
        return None

    def log(self, _level, _message):
        return None


class LoggingRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logging_module = importlib.import_module("speech_translate._logging")
        self.original_logger = self.logging_module.logger
        self.original_stderr = sys.stderr
        self.original_file_id = self.logging_module.FILE_ID
        self.original_console_id = self.logging_module.CONSOLE_ID
        self.original_recent_stderr = list(self.logging_module.recent_stderr)
        self.logging_module.recent_stderr.clear()

    def tearDown(self) -> None:
        self.logging_module.logger = self.original_logger
        self.logging_module.FILE_ID = self.original_file_id
        self.logging_module.CONSOLE_ID = self.original_console_id
        self.logging_module.recent_stderr[:] = self.original_recent_stderr
        sys.stderr = self.original_stderr

    def test_init_logging_uses_original_stderr_for_loguru_console_sink(self) -> None:
        fake_logger = FakeLoguruLogger()
        self.logging_module.logger = fake_logger

        self.logging_module.init_logging("DEBUG")

        self.assertIsInstance(sys.stderr, self.logging_module.StreamStderrToLogger)
        self.assertEqual(len(fake_logger.add_calls), 2)
        console_sink, console_kwargs = fake_logger.add_calls[0]
        self.assertIs(console_sink, self.original_stderr)
        self.assertEqual(console_kwargs["level"], "DEBUG")
        file_sink, _file_kwargs = fake_logger.add_calls[1]
        self.assertTrue(str(file_sink).endswith(".log"))

    def test_stream_stderr_to_logger_collects_progress_without_crashing(self) -> None:
        captured = []

        class StubLogger:
            def log(self, level, message):
                captured.append((level, message))

            def error(self, message):
                captured.append(("ERROR", message))

        self.logging_module.logger = StubLogger()
        sink = self.logging_module.StreamStderrToLogger()
        sink.write("50%|#####| 1.0KiB/2.0KiB [00:01<00:01, 1.0KiB/s]\n")

        self.assertTrue(captured)
        self.assertEqual(captured[0][0], "INFO")
        self.assertTrue(any("50%" in line for line in self.logging_module.recent_stderr))


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
