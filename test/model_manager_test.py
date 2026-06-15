from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.model_manager import ModelManagerController
from speech_translate.utils.whisper.download_runtime import DownloadProgressSnapshot


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {"model_f_import": "small", "dir_model": "auto"}
        self.saved = {}

    def save_key(self, key: str, value):
        self.saved[key] = value
        self.cache[key] = value


class FakeBridge:
    def __init__(self) -> None:
        self.messages = []
        self.progress = []

    def reset_task_state(self, title: str):
        self.messages.append(("reset", title))

    def update_task_message(self, message: str, source: str = "general"):
        self.messages.append((source, message))

    def update_task_progress(self, value: float, source: str = "general"):
        self.progress.append((source, value))

    def update_task_error(self, error: str):
        self.messages.append(("error", error))

    def finish_task(self, message: str):
        self.messages.append(("finish", message))

    def get_settings_snapshot(self):
        return {
            "transcribe_mw": True,
            "translate_mw": True,
            "tl_engine_mw": "Google Translate",
            "model_mw": "small",
            "model_f_import": "small",
        }


class ModelManagerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = FakeSettings()
        self.bridge = FakeBridge()
        self.controller = ModelManagerController(self.bridge, self.settings, lambda: None)

    def test_normalize_model_key_maps_display_name(self) -> None:
        self.assertEqual(self.controller.normalize_model_key("⚡ Tiny [1GB VRAM] (Fastest)"), "tiny")
        self.assertEqual(self.controller.normalize_model_key("small"), "small")

    def test_handle_task_message_updates_runtime_state(self) -> None:
        self.controller.handle_task_message("Loading model cache for small")
        self.assertTrue(self.controller.model_load_running)
        self.assertFalse(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_key, "small")

        self.controller.handle_task_message("Model ready: small")
        self.assertFalse(self.controller.model_load_running)
        self.assertTrue(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model ready: small")

    def test_handle_task_message_uses_source_aware_model_load_path(self) -> None:
        self.controller.handle_task_message("Loading model cache for medium", source="model-load")
        self.assertTrue(self.controller.model_load_running)
        self.assertEqual(self.controller.runtime_model_key, "medium")

        self.controller.handle_task_message("Model ready: medium", source="model-load")
        self.assertFalse(self.controller.model_load_running)
        self.assertTrue(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model ready: medium")

    def test_handle_task_message_ignores_model_download_source(self) -> None:
        self.controller.runtime_model_key = "small"
        self.controller.handle_task_message("DL small: 10 MB", source="model-download")
        self.assertEqual(self.controller.runtime_model_key, "small")

    def test_handle_recording_status_marks_runtime_ready(self) -> None:
        self.controller.runtime_model_key = "medium"
        self.controller.handle_recording_status({"status": "Recording..."})
        self.assertFalse(self.controller.model_load_running)
        self.assertTrue(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model ready: medium")

    def test_build_runtime_model_state_reflects_loading_flag(self) -> None:
        self.controller.runtime_model_key = "large-v3"
        self.controller.model_load_running = True
        self.controller.runtime_model_loaded = False
        state = self.controller.build_runtime_model_state()
        self.assertEqual(
            state,
            {
                "key": "large-v3",
                "loading": True,
                "loaded": False,
                "message": self.controller.runtime_model_message,
            },
        )

    def test_mark_runtime_model_pending_sets_loading_state(self) -> None:
        self.controller.mark_runtime_model_pending("medium")
        self.assertEqual(self.controller.runtime_model_key, "medium")
        self.assertTrue(self.controller.model_load_running)
        self.assertFalse(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Loading model cache for medium")

    def test_mark_runtime_model_pending_can_mark_ready_state(self) -> None:
        self.controller.mark_runtime_model_pending("medium", loaded=True)
        self.assertEqual(self.controller.runtime_model_key, "medium")
        self.assertFalse(self.controller.model_load_running)
        self.assertTrue(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model ready: medium")

    def test_mark_runtime_model_ready_sets_ready_state(self) -> None:
        self.controller.mark_runtime_model_ready("large-v3")
        self.assertEqual(self.controller.runtime_model_key, "large-v3")
        self.assertFalse(self.controller.model_load_running)
        self.assertTrue(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model ready: large-v3")

    def test_mark_runtime_model_ready_prefers_explicit_message(self) -> None:
        self.controller.mark_runtime_model_ready("large-v3", message="ready now")
        self.assertEqual(self.controller.runtime_model_message, "ready now")

    def test_mark_runtime_model_failed_sets_failure_state(self) -> None:
        self.controller.model_load_running = True
        self.controller.runtime_model_loaded = True
        self.controller.mark_runtime_model_failed("Model load failed: boom")
        self.assertFalse(self.controller.model_load_running)
        self.assertFalse(self.controller.runtime_model_loaded)
        self.assertEqual(self.controller.runtime_model_message, "Model load failed: boom")

    def test_check_model_normalizes_display_name_before_verification(self) -> None:
        previous_verify = self.controller.verify_model_status
        captured = {}
        try:
            self.controller.verify_model_status = lambda engine, model_key, model_dir: captured.update(
                {"engine": engine, "model_key": model_key, "model_dir": model_dir}
            ) or (True, "")
            self.controller.check_model("⚡ Tiny [1GB VRAM] (Fastest)", engine="Whisper")
        finally:
            self.controller.verify_model_status = previous_verify

        self.assertEqual(captured["engine"], "whisper")
        self.assertEqual(captured["model_key"], "tiny")
        self.assertEqual(self.controller.model_manager_model, "tiny")

    def test_download_model_delegates_to_shared_download_api(self) -> None:
        from speech_translate import model_manager as model_manager_module

        class InlineThread:
            def __init__(self, target, daemon=True):
                self._target = target
                self.daemon = daemon

            def start(self):
                self._target()

        class FakeDownloadApi:
            def __init__(self) -> None:
                self.calls = []

            def verify_model_whisper(self, model_key, model_dir):
                return True

            def verify_model_faster_whisper(self, model_key, model_dir):
                return True

            def download_model(self, model_key, **kwargs):
                self.calls.append((model_key, kwargs))
                kwargs["progress_callback"](
                    DownloadProgressSnapshot(
                        current_bytes=1024,
                        total_bytes=2048,
                        progress=42.0,
                        speed_bytes_per_sec=512.0,
                        speed_text="512 B/s",
                        size_text="1.0 KB/2.0 KB",
                        elapsed_seconds=2.0,
                    )
                )
                kwargs["reporter"].update_task_message("Downloading test model")
                kwargs["reporter"].update_task_progress(42.0)
                return True

        fake_download_api = FakeDownloadApi()
        controller = ModelManagerController(
            self.bridge,
            self.settings,
            lambda: None,
            whisper_download_getter=lambda: fake_download_api,
        )

        previous_thread = model_manager_module.Thread
        try:
            model_manager_module.Thread = InlineThread
            result = controller.download_model("small", engine="whisper")
        finally:
            model_manager_module.Thread = previous_thread

        self.assertTrue(result["ok"])
        self.assertEqual(fake_download_api.calls[0][0], "small")
        self.assertEqual(fake_download_api.calls[0][1]["progress_floor"], 5.0)
        self.assertEqual(fake_download_api.calls[0][1]["progress_ceiling"], 90.0)
        cached = controller.model_status_cache["whisper:small"]
        self.assertTrue(cached["downloaded"])
        self.assertFalse(cached["downloading"])
        self.assertEqual(cached["progress"], 100.0)
        self.assertIn(("finish", "Model downloaded: small (whisper)"), self.bridge.messages)

    def test_load_runtime_model_clears_loading_state_after_success(self) -> None:
        from speech_translate import model_manager as model_manager_module

        class InlineThread:
            def __init__(self, target, daemon=True):
                self._target = target
                self.daemon = daemon

            def start(self):
                self._target()

        class FakeWhisperLoadApi:
            def get_model_args(self, settings_snapshot):
                return {"device": "cpu", "download_root": "D:/models"}

            def get_model(self, *args, **kwargs):
                return ("tc", None, object(), None, object())

        controller = ModelManagerController(
            self.bridge,
            self.settings,
            lambda: FakeWhisperLoadApi(),
        )

        previous_thread = model_manager_module.Thread
        try:
            model_manager_module.Thread = InlineThread
            result = controller.load_runtime_model("tiny")
        finally:
            model_manager_module.Thread = previous_thread

        self.assertTrue(result["ok"])
        self.assertFalse(controller.model_load_running)
        self.assertTrue(controller.runtime_model_loaded)
        self.assertEqual(controller.runtime_model_key, "tiny")
        self.assertIn(("finish", "Model ready: tiny"), self.bridge.messages)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
