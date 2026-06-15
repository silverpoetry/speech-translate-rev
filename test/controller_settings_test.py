from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.controller_settings import (
    build_recording_controller_settings,
    build_runtime_model_load_settings,
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


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
