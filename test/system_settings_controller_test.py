from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.system_settings_controller import SystemSettingsController


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {
            "dir_export": "auto",
            "dir_log": "auto",
            "dir_model": "auto",
            "log_level": "INFO",
            "selenium_chrome_user_data_dir": "",
        }
        self.saved = {}

    def save_key(self, key: str, value):
        self.saved[key] = value
        self.cache[key] = value


class FakeModelManager:
    def __init__(self) -> None:
        self.cleared = False

    def clear_model_status_cache(self):
        self.cleared = True

    def resolve_model_dir(self):
        return "D:\\models"


class FakeWindow:
    def __init__(self, selected=None) -> None:
        self.selected = selected if selected is not None else ["D:\\chosen"]
        self.calls = []

    def create_file_dialog(self, file_dialog, directory=None):
        self.calls.append((file_dialog, directory))
        return self.selected


class FakeBridge:
    def __init__(self, window=None) -> None:
        self.window = window

    def get_window(self):
        return self.window


class SystemSettingsControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = FakeSettings()
        self.bridge = FakeBridge()
        self.model_manager = FakeModelManager()
        self.controller = SystemSettingsController(
            self.bridge,
            self.settings,
            {
                "dir_debug": "D:\\debug",
                "dir_export": "D:\\exports",
                "dir_log": "D:\\logs",
                "dir_user": "D:\\user",
            },
            self.model_manager,
        )

    def test_resolve_export_dir_uses_auto_default(self) -> None:
        self.assertEqual(self.controller.resolve_export_dir(), "D:\\exports")
        self.settings.cache["dir_export"] = "D:\\custom"
        self.assertEqual(self.controller.resolve_export_dir(), "D:\\custom")

    def test_set_setting_normalizes_selenium_compound_payload(self) -> None:
        result = self.controller.set_setting(
            "selenium_settings",
            {
                "compact_level": 99,
                "z_order_mode": "invalid",
                "auto_close_on_task_done": False,
                "chrome_user_data_dir": " D:\\chrome ",
            },
        )
        self.assertEqual(result["value"]["selenium_compact_level"], 3)
        self.assertEqual(result["value"]["selenium_z_order_mode"], "behind-main")
        self.assertFalse(result["value"]["selenium_auto_close_on_task_done"])
        self.assertEqual(result["value"]["selenium_chrome_user_data_dir"], "D:\\chrome")

    def test_set_record_setting_normalizes_device_preference(self) -> None:
        result = self.controller.set_record_setting("model_device_preference", "GPU")
        self.assertEqual(result["value"], "auto")
        result = self.controller.set_record_setting("model_device_preference", "cuda")
        self.assertEqual(result["value"], "cuda")

    def test_set_setting_normalizes_individual_selenium_values(self) -> None:
        compact = self.controller.set_setting("selenium_compact_level", 99)
        chrome_dir = self.controller.set_setting("selenium_chrome_user_data_dir", " D:\\chrome-profile ")
        self.assertEqual(compact["value"], 3)
        self.assertEqual(chrome_dir["value"], "D:\\chrome-profile")

    def test_select_directory_updates_setting_and_clears_model_cache(self) -> None:
        self.bridge.window = FakeWindow()
        with patch("speech_translate.system_settings_controller.create_file_dialog", return_value=["D:\\chosen"]) as create_dialog:
            result = self.controller.select_directory("model")

        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], "D:\\chosen")
        self.assertEqual(self.settings.saved["dir_model"], "D:\\chosen")
        self.assertTrue(self.model_manager.cleared)
        create_dialog.assert_called_once_with(self.bridge.window, dialog_kind="folder", directory="D:\\models")

    def test_select_directory_updates_log_setting(self) -> None:
        self.bridge.window = FakeWindow(["D:\\custom-logs"])
        with patch("speech_translate.system_settings_controller.create_file_dialog", return_value=["D:\\custom-logs"]) as create_dialog:
            result = self.controller.select_directory("log")

        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], "D:\\custom-logs")
        self.assertEqual(self.settings.saved["dir_log"], "D:\\custom-logs")
        create_dialog.assert_called_once_with(self.bridge.window, dialog_kind="folder", directory="D:\\logs")

    def test_open_directory_supports_selenium_chrome_profile(self) -> None:
        with patch("speech_translate.system_settings_controller.open_folder") as open_folder:
            result = self.controller.open_directory("selenium_chrome")

        self.assertEqual(result["target"], "D:\\user\\selenium_chrome_profile")
        open_folder.assert_called_once_with("D:\\user\\selenium_chrome_profile")

    def test_get_log_content_reads_file_and_truncates_large_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            log_file = log_dir / "current.log"
            log_file.write_text("x" * 210000, encoding="utf-8")
            controller = SystemSettingsController(
                self.bridge,
                self.settings,
                {
                    "dir_debug": "D:\\debug",
                    "dir_export": "D:\\exports",
                    "dir_log": str(log_dir),
                    "dir_user": "D:\\user",
                },
                self.model_manager,
            )
            with patch("speech_translate._logging.current_log", "current.log"):
                content = controller.get_log_content()

        self.assertEqual(len(content), 200000)

    def test_set_setting_reconfigures_logging_when_log_dir_changes(self) -> None:
        with patch("speech_translate._logging.change_log_level") as change_log_level:
            result = self.controller.set_setting("dir_log", "D:\\custom-logs")

        self.assertEqual(result["value"], "D:\\custom-logs")
        change_log_level.assert_called_once_with("INFO", "D:\\custom-logs")

    def test_clear_log_uses_resolved_log_dir(self) -> None:
        self.settings.cache["dir_log"] = "D:\\custom-logs"
        with (
            patch("speech_translate._logging.clear_current_log_file") as clear_current_log_file,
            patch.object(self.controller, "refresh_log", return_value={"content": "", "file": "current.log"}) as refresh_log,
        ):
            result = self.controller.clear_log()

        clear_current_log_file.assert_called_once_with("D:\\custom-logs", "INFO")
        refresh_log.assert_called_once()
        self.assertEqual(result["file"], "current.log")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
