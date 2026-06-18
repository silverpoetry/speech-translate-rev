from __future__ import annotations

import os
import sys
import tempfile
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.model_manager_workflows import (
    ModelDownloadRequest,
    ModelDownloadService,
    RuntimeModelLoadRequest,
    RuntimeModelLoadService,
)
from speech_translate.utils.whisper.download_runtime import DownloadProgressSnapshot


class FakeBridge:
    def __init__(self) -> None:
        self.messages = []
        self.progress = []

    def reset_task_state(self, title: str) -> None:
        self.messages.append(("reset", title))

    def update_task_message(self, message: str, source: str = "general") -> None:
        self.messages.append((source, message))

    def update_task_progress(self, value: float, source: str = "general") -> None:
        self.progress.append((source, value))

    def finish_task(self, message: str) -> None:
        self.messages.append(("finish", message))

    def update_task_error(self, error: str) -> None:
        self.messages.append(("error", error))


class ModelManagerWorkflowTests(unittest.TestCase):
    def test_model_download_service_runs_download_and_verification(self) -> None:
        bridge = FakeBridge()
        cached = []
        verified = []

        class FakeDownloadApi:
            def download_model(self, model_key: str, **kwargs):
                kwargs["progress_callback"](
                    DownloadProgressSnapshot(
                        current_bytes=1024,
                        total_bytes=2048,
                        progress=42.0,
                        speed_bytes_per_sec=512.0,
                        speed_text="512 B/s",
                        size_text="1.0 KB/2.0 KB",
                        elapsed_seconds=1.0,
                    )
                )
                kwargs["reporter"].update_task_message("Downloading model")
                kwargs["reporter"].update_task_progress(42.0)
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            service = ModelDownloadService(
                bridge,
                whisper_download_getter=lambda: FakeDownloadApi(),
                verify_model_status=lambda engine, model_key, model_dir: verified.append((engine, model_key, model_dir)) or (True, ""),
                cache_model_status=lambda *args, **kwargs: cached.append((args, kwargs)),
                resolve_model_dir=lambda: temp_dir,
            )

            service.run(ModelDownloadRequest(model_key="small", engine="whisper"))

        self.assertTrue(any(entry[0][:3] == ("whisper", "small", False) for entry in cached))
        self.assertEqual(verified[0][:2], ("whisper", "small"))
        self.assertIn(("finish", "Model downloaded: small (whisper)"), bridge.messages)
        self.assertIn(("model-download", 100), bridge.progress)

    def test_runtime_model_load_service_reports_cached_bundle_path(self) -> None:
        bridge = FakeBridge()

        class FakeLoadApi:
            def get_model_args(self, settings_snapshot):
                return {"device": "cpu"}

            def is_model_bundle_cached(self, *args, **kwargs):
                return True

            def get_model(self, *args, **kwargs):
                return ("tc", None, object(), None, object())

        service = RuntimeModelLoadService(
            bridge,
            whisper_loader_getter=lambda: FakeLoadApi(),
            normalize_engine_name=lambda value: value,
            get_settings_snapshot=lambda: {
                "transcribe_mw": True,
                "translate_mw": True,
                "tl_engine_mw": "Google Translate",
                "model_mw": "small",
                "model_f_import": "small",
            },
        )

        service.run(RuntimeModelLoadRequest(model_key="tiny"))

        self.assertIn(("model-load", "Using cached runtime bundle for tiny"), bridge.messages)
        self.assertIn(("model-load", 80), bridge.progress)
        self.assertIn(("finish", "Model ready: tiny"), bridge.messages)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
