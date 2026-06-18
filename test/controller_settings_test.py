from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.controller_settings import (
    build_compound_setting_response,
    build_recording_controller_settings,
    build_runtime_model_load_settings,
    build_selenium_settings,
    build_setting_response,
    normalize_import_setting_value,
    normalize_record_setting_value,
    normalize_system_setting_value,
)


class ControllerSettingsTests(unittest.TestCase):
    def test_build_recording_controller_settings_copies_and_normalizes_snapshot(self) -> None:
        original_snapshot = {
            "input": "speaker",
            "source_lang_mw": "Japanese",
            "target_lang_mw": "English",
            "tl_engine_mw": "Selenium Chrome Translate",
            "model_mw": "tiny",
            "transcribe_mw": True,
            "translate_mw": True,
            "selenium_auto_close_on_task_done": True,
        }

        settings = build_recording_controller_settings(
            original_snapshot,
            default_device="mic",
            default_lang_source="Chinese",
            default_lang_target="French",
            default_engine="Google Translate",
            default_is_tc=False,
            default_is_tl=False,
            normalize_engine_name=lambda value: value.strip(),
            normalize_model_key=lambda value: value.upper(),
        )
        original_snapshot["input"] = "mic"

        self.assertEqual(settings.device, "speaker")
        self.assertEqual(settings.lang_source, "Japanese")
        self.assertEqual(settings.lang_target, "English")
        self.assertEqual(settings.engine, "Selenium Chrome Translate")
        self.assertEqual(settings.model_name_tc, "TINY")
        self.assertTrue(settings.should_auto_close_selenium)
        self.assertEqual(settings.snapshot["input"], "speaker")

    def test_build_runtime_model_load_settings_updates_model_keys_on_copied_snapshot(self) -> None:
        original_snapshot = {
            "transcribe_mw": True,
            "translate_mw": False,
            "tl_engine_mw": "Google Translate",
            "model_mw": "small",
            "model_f_import": "small",
        }

        settings = build_runtime_model_load_settings(
            original_snapshot,
            model_key="tiny",
            normalize_engine_name=lambda value: value,
        )

        self.assertEqual(settings.model_key, "tiny")
        self.assertEqual(settings.snapshot["model_mw"], "tiny")
        self.assertEqual(settings.snapshot["model_f_import"], "tiny")
        self.assertEqual(original_snapshot["model_mw"], "small")
        self.assertTrue(settings.transcribe_enabled)
        self.assertFalse(settings.translate_enabled)
        self.assertFalse(settings.tl_engine_whisper)

    def test_build_selenium_settings_normalizes_and_clamps_values(self) -> None:
        settings = build_selenium_settings(
            {
                "compact_level": "99",
                "z_order_mode": " INVALID ",
                "auto_close_on_task_done": 0,
                "chrome_user_data_dir": " D:\\chrome ",
            }
        )

        self.assertEqual(settings.compact_level, 3)
        self.assertEqual(settings.z_order_mode, "behind-main")
        self.assertFalse(settings.auto_close_on_task_done)
        self.assertEqual(settings.chrome_user_data_dir, "D:\\chrome")
        self.assertEqual(settings.as_settings_updates()["selenium_compact_level"], 3)

    def test_normalize_system_setting_value_handles_invalid_compact_level(self) -> None:
        self.assertEqual(normalize_system_setting_value("model_mw", "⚡ Tiny [1GB VRAM] (Fastest)"), "tiny")
        self.assertEqual(normalize_system_setting_value("selenium_compact_level", "bad"), 2)
        self.assertEqual(normalize_system_setting_value("selenium_z_order_mode", "BOTTOM"), "bottom")
        self.assertEqual(normalize_system_setting_value("selenium_chrome_user_data_dir", " D:\\data "), "D:\\data")

    def test_normalize_import_setting_value_normalizes_model_key(self) -> None:
        self.assertEqual(normalize_import_setting_value("model_f_import", "⛵ Small [2GB VRAM] (Moderate)"), "small")
        self.assertEqual(normalize_import_setting_value("tl_engine_f_import", "Google Translate"), "Google Translate")

    def test_normalize_record_setting_value_normalizes_device_preference(self) -> None:
        self.assertEqual(normalize_record_setting_value("model_device_preference", "GPU"), "auto")
        self.assertEqual(normalize_record_setting_value("model_device_preference", "cuda"), "cuda")

    def test_build_setting_responses_project_snapshot_values(self) -> None:
        snapshot = {
            "log_level": "DEBUG",
            "selenium_compact_level": 2,
            "selenium_z_order_mode": "behind-main",
        }

        self.assertEqual(build_setting_response("log_level", snapshot), {"key": "log_level", "value": "DEBUG"})
        self.assertEqual(
            build_compound_setting_response(
                "selenium_settings",
                snapshot,
                {
                    "selenium_compact_level": 3,
                    "selenium_z_order_mode": "normal",
                },
            ),
            {
                "key": "selenium_settings",
                "value": {
                    "selenium_compact_level": 2,
                    "selenium_z_order_mode": "behind-main",
                },
            },
        )


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
