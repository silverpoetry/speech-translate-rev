from __future__ import annotations

import os
import sys
import tempfile
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.linker import bc
from speech_translate.utils.whisper.download import TaskReporter, whisper_download_headless


class FakeUrlResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def info(self):
        return {"Content-Length": "4"}

    def read(self, _buffer_size):
        return b""


class WhisperDownloadTests(unittest.TestCase):
    def test_whisper_download_headless_runs_cancel_callback(self) -> None:
        from speech_translate.utils.whisper import download as download_module

        previous_urlopen = download_module.urllib.request.urlopen
        previous_callback_starter = download_module.start_optional_callback
        previous_cancel = bc.cancel_dl
        cancelled = []
        reporter_events = []
        try:
            download_module.urllib.request.urlopen = lambda _url: FakeUrlResponse()
            download_module.start_optional_callback = lambda callback: callback() if callback is not None else None
            bc.cancel_dl = True

            with tempfile.TemporaryDirectory() as temp_dir:
                result = whisper_download_headless(
                    "small",
                    "https://example.com/0123456789abcdef/model.pt",
                    temp_dir,
                    lambda: cancelled.append("cancelled"),
                    None,
                    None,
                    reporter=TaskReporter(
                        reset_task_state=lambda title: reporter_events.append(("reset", title)),
                        finish_task=lambda message: reporter_events.append(("finish", message)),
                    ),
                )
        finally:
            download_module.urllib.request.urlopen = previous_urlopen
            download_module.start_optional_callback = previous_callback_starter
            bc.cancel_dl = previous_cancel

        self.assertFalse(result)
        self.assertEqual(cancelled, ["cancelled"])
        self.assertIn(("finish", "Download Cancelled"), reporter_events)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
