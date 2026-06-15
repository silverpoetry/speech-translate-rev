from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.whisper.download_runtime import (
    build_download_progress_snapshot,
    format_bytes,
    monitor_threaded_download,
    path_size,
)


class DownloadRuntimeTests(unittest.TestCase):
    def test_format_bytes_and_path_size_handle_basic_inputs(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"abcd")
            temp_path = handle.name
        try:
            self.assertEqual(path_size(temp_path), 4)
            self.assertEqual(format_bytes(0), "0 B")
            self.assertEqual(format_bytes(1536), "1.5 KB")
        finally:
            os.remove(temp_path)

    def test_build_download_progress_snapshot_uses_time_fallback_when_total_unknown(self) -> None:
        snapshot = build_download_progress_snapshot(
            current_bytes=1024,
            total_bytes=0,
            started_at=10.0,
            previous_bytes=0,
            previous_time=10.0,
            current_time=12.0,
            progress_floor=5.0,
            progress_ceiling=90.0,
            allow_time_fallback=True,
        )

        self.assertEqual(snapshot.size_text, "1.0 KB")
        self.assertGreaterEqual(snapshot.progress, 5.0)
        self.assertLessEqual(snapshot.progress, 90.0)
        self.assertEqual(snapshot.speed_text, "512 B/s")

    def test_monitor_threaded_download_can_cancel_running_worker(self) -> None:
        cancelled_threads = []

        def download_fn() -> None:
            time.sleep(0.05)

        result = monitor_threaded_download(
            download_fn=download_fn,
            observe_path="",
            total_bytes=0,
            cancel_requested=lambda: True,
            cancel_handler=lambda thread: cancelled_threads.append(thread),
            poll_interval=0.01,
        )

        self.assertTrue(result.cancelled)
        self.assertIsNone(result.error)
        self.assertEqual(len(cancelled_threads), 1)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
