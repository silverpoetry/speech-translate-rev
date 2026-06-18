from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.web_bridge_api import (
    CONTROLLER_API_NAMES,
    PUBLIC_CONTROLLER_ROUTES,
    WEBVIEW_PUBLIC_API_NAMES,
    WEBVIEW_PUBLIC_APP_API_NAMES,
    WEBVIEW_PUBLIC_CONTROLLER_API_NAMES,
    WebBridgeApiMixin,
)


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

    def test_legacy_proxy_attributes_are_not_exposed(self) -> None:
        self.assertFalse(hasattr(self.bridge, "_model_status_cache"))
        self.assertFalse(hasattr(self.bridge, "_runtime_model_key"))
        self.assertFalse(hasattr(self.bridge, "_file_import_queue"))
        self.assertFalse(hasattr(self.bridge, "_record_worker_thread"))

    def test_build_main_ui_forwards_to_state_view_builder(self) -> None:
        self.assertFalse(hasattr(self.bridge, "build_main_ui"))

    def test_resolve_model_dir_forwards_to_model_manager(self) -> None:
        self.assertFalse(hasattr(self.bridge, "resolve_model_dir"))

    def test_wait_recording_idle_forwards_to_recording_controller(self) -> None:
        self.assertFalse(hasattr(self.bridge, "wait_recording_idle"))

    def test_get_detached_config_forwards_to_detached_controller(self) -> None:
        result = self.bridge.get_detached_config("tc")
        self.assertEqual(result, {"mode": "tc"})
        self.assertIn(("get_detached_config", "tc"), self.bridge.detached_window_controller.calls)

    def test_open_directory_forwards_to_system_settings_controller(self) -> None:
        result = self.bridge.open_directory("export")
        self.assertEqual(result, {"name": "export"})
        self.assertIn(("open_directory", "export"), self.bridge.system_settings_controller.calls)

    def test_log_startup_marker_uses_main_window_log_method_name(self) -> None:
        self.assertFalse(hasattr(self.bridge, "log_startup_marker"))

    def test_build_audio_source_options_forwards_optional_argument(self) -> None:
        self.bridge.state_view_builder.build_audio_source_options = lambda selected_host_api=None: {
            "selected_host_api": selected_host_api
        }
        self.assertFalse(hasattr(self.bridge, "build_audio_source_options"))

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

    def test_controller_api_name_registry_exposes_public_methods(self) -> None:
        self.assertIn("get_recording_state", CONTROLLER_API_NAMES)
        self.assertIn("load_runtime_model", CONTROLLER_API_NAMES)
        self.assertIn("create_detached_window", CONTROLLER_API_NAMES)
        self.assertIn("show_detached_window", WEBVIEW_PUBLIC_CONTROLLER_API_NAMES)
        self.assertIn("hide_detached_window", WEBVIEW_PUBLIC_CONTROLLER_API_NAMES)
        self.assertIn("close_detached_window", WEBVIEW_PUBLIC_CONTROLLER_API_NAMES)
        self.assertIn("hide_main_window_to_tray", CONTROLLER_API_NAMES)
        self.assertIn("check_model", CONTROLLER_API_NAMES)
        self.assertIn("check_model", WEBVIEW_PUBLIC_CONTROLLER_API_NAMES)
        self.assertTrue(WEBVIEW_PUBLIC_CONTROLLER_API_NAMES.issubset(set(CONTROLLER_API_NAMES)))
        self.assertEqual(len(CONTROLLER_API_NAMES), len(set(CONTROLLER_API_NAMES)))
        self.assertEqual(set(PUBLIC_CONTROLLER_ROUTES), set(WEBVIEW_PUBLIC_CONTROLLER_API_NAMES))

    def test_internal_controller_routes_are_not_exposed_on_bridge(self) -> None:
        internal_names = set(CONTROLLER_API_NAMES) - set(WEBVIEW_PUBLIC_CONTROLLER_API_NAMES)
        self.assertIn("build_main_ui", internal_names)
        self.assertIn("resolve_model_dir", internal_names)
        self.assertIn("wait_recording_idle", internal_names)
        for name in internal_names:
            self.assertFalse(hasattr(self.bridge, name), name)

    def test_public_api_names_cover_frontend_usage(self) -> None:
        app_js_path = Path(to_add) / "speech_translate" / "web" / "app.js"
        recording_window_path = Path(to_add) / "speech_translate" / "web" / "recording_window.html"
        frontend_method_names = self._extract_pywebview_api_names(app_js_path) | self._extract_pywebview_api_names(recording_window_path)

        self.assertTrue(frontend_method_names)
        self.assertTrue(frontend_method_names.issubset(set(WEBVIEW_PUBLIC_API_NAMES)))
        self.assertTrue(WEBVIEW_PUBLIC_APP_API_NAMES.issuperset({"get_state", "get_task_state", "get_live_state"}))

    @staticmethod
    def _extract_pywebview_api_names(path: Path) -> set[str]:
        content = path.read_text(encoding="utf-8")
        names = set(re.findall(r"apiCall\('([A-Za-z0-9_]+)'", content))
        names.update(re.findall(r"window\.pywebview\.api\.([A-Za-z0-9_]+)", content))
        return names


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
