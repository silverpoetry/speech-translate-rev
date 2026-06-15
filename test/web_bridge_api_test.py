from __future__ import annotations

import os
import sys
import tempfile
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.web_bridge_api import WebBridgeApiMixin


class DummyController:
    def __init__(self) -> None:
        self.model_status_cache = {}
        self.model_download_running = False
        self.model_load_running = False
        self.runtime_model_key = "small"
        self.runtime_model_loaded = False
        self.runtime_model_message = ""
        self.model_manager_engine = "whisper"
        self.model_manager_model = "small"
        self.file_import_queue = []
        self.processing_queue = []
        self.record_worker_thread = None
        self.recording_state = {"status": "Idle"}
        self.calls = []

    def normalize_model_key(self, value):
        self.calls.append(("normalize_model_key", value))
        return f"n:{value}"

    def resolve_model_dir(self):
        self.calls.append(("resolve_model_dir",))
        return "D:/models"

    def wait_recording_idle(self, timeout_s=12.0):
        self.calls.append(("wait_recording_idle", timeout_s))
        return True

    def build_main_ui(self):
        self.calls.append(("build_main_ui",))
        return {"ok": True}

    def log_startup_marker(self, marker):
        self.calls.append(("log_startup_marker", marker))
        return {"marker": marker}

    def get_detached_config(self, mode):
        self.calls.append(("get_detached_config", mode))
        return {"mode": mode}

    def open_directory(self, name):
        self.calls.append(("open_directory", name))
        return {"name": name}


class DummyBridge(WebBridgeApiMixin):
    def __init__(self) -> None:
        self.model_manager_controller = DummyController()
        self.import_queue_controller = DummyController()
        self.recording_controller = DummyController()
        self.system_settings_controller = DummyController()
        self.state_view_builder = DummyController()
        self.detached_window_controller = DummyController()
        self.main_window_controller = DummyController()


class WebBridgeApiMixinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = DummyBridge()

    def test_model_status_cache_property_proxies_to_controller(self) -> None:
        self.bridge._model_status_cache = {"whisper:small": {"downloaded": True}}
        self.assertEqual(self.bridge.model_manager_controller.model_status_cache["whisper:small"]["downloaded"], True)
        self.assertEqual(self.bridge._model_status_cache["whisper:small"]["downloaded"], True)

    def test_runtime_model_key_property_proxies_to_controller(self) -> None:
        self.bridge._runtime_model_key = "medium"
        self.assertEqual(self.bridge._runtime_model_key, "medium")
        self.assertEqual(self.bridge.model_manager_controller.runtime_model_key, "medium")

    def test_build_main_ui_forwards_to_state_view_builder(self) -> None:
        result = self.bridge.build_main_ui()
        self.assertEqual(result, {"ok": True})
        self.assertIn(("build_main_ui",), self.bridge.state_view_builder.calls)

    def test_resolve_model_dir_forwards_to_model_manager(self) -> None:
        self.assertEqual(self.bridge.resolve_model_dir(), "D:/models")
        self.assertIn(("resolve_model_dir",), self.bridge.model_manager_controller.calls)

    def test_wait_recording_idle_forwards_to_recording_controller(self) -> None:
        self.assertTrue(self.bridge.wait_recording_idle(timeout_s=1.5))
        self.assertIn(("wait_recording_idle", 1.5), self.bridge.recording_controller.calls)

    def test_get_detached_config_forwards_to_detached_controller(self) -> None:
        result = self.bridge.get_detached_config("tc")
        self.assertEqual(result, {"mode": "tc"})
        self.assertIn(("get_detached_config", "tc"), self.bridge.detached_window_controller.calls)

    def test_open_directory_forwards_to_system_settings_controller(self) -> None:
        result = self.bridge.open_directory("export")
        self.assertEqual(result, {"name": "export"})
        self.assertIn(("open_directory", "export"), self.bridge.system_settings_controller.calls)

    def test_log_startup_marker_uses_main_window_log_method_name(self) -> None:
        self.bridge.log_startup_marker("boot")
        self.assertIn(("log_startup_marker", "boot"), self.bridge.main_window_controller.calls)

    def test_build_audio_source_options_forwards_optional_argument(self) -> None:
        self.bridge.state_view_builder.build_audio_source_options = lambda selected_host_api=None: {
            "selected_host_api": selected_host_api
        }
        self.assertEqual(
            self.bridge.build_audio_source_options("WASAPI"),
            {"selected_host_api": "WASAPI"},
        )

    def test_path_size_handles_single_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"abcd")
            temp_path = handle.name
        try:
            self.assertEqual(self.bridge._path_size(temp_path), 4)
        finally:
            os.remove(temp_path)

    def test_fmt_bytes_uses_readable_units(self) -> None:
        self.assertEqual(self.bridge._fmt_bytes(1536), "1.5 KB")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
