from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.setting import _migrate_legacy_setting_keys, default_setting


class DefaultSettingTests(unittest.TestCase):
    def test_migrate_legacy_threshold_auto_mode_keys(self) -> None:
        migrated = _migrate_legacy_setting_keys(
            {
                "threshold_auto_mode_mic": "1",
                "threshold_auto_mode_speaker": "2",
            }
        )

        self.assertEqual(migrated["threshold_auto_level_mic"], "1")
        self.assertEqual(migrated["threshold_auto_level_speaker"], "2")

    def test_migrate_legacy_threshold_auto_mode_keys_preserves_current_values(self) -> None:
        migrated = _migrate_legacy_setting_keys(
            {
                "threshold_auto_mode_mic": "1",
                "threshold_auto_level_mic": "3",
            }
        )

        self.assertEqual(migrated["threshold_auto_level_mic"], "3")

    def test_migrate_legacy_setting_keys_removes_obsolete_webview_migration_keys(self) -> None:
        migrated = _migrate_legacy_setting_keys(
            {
                "checkUpdateOnStart": False,
                "theme": "sun-valley-light",
                "show_audio_visualizer_in_record": True,
                "show_audio_visualizer_in_setting": True,
                "sw_size": "1100x630",
                "model_f_alignment": "small",
                "model_f_refinement": "small",
                "target_lang_f_result": "Indonesian",
                "tl_engine_f_result": "Google Translate",
                "supress_device_warning": True,
                "debug_recorded_audio": True,
                "remove_repetition_result_refinement": False,
                "remove_repetition_result_alignment": False,
                "auto_verify_model_on_first_setting_open": False,
                "ex_tc_no_tooltip": 0,
                "ex_tl_no_tooltip": 0,
            }
        )

        self.assertEqual(migrated, {})

    def test_main_window_textbox_font_color_defaults_exist(self) -> None:
        self.assertEqual(default_setting["tb_mw_tc_font_color"], "#FFFFFF")
        self.assertEqual(default_setting["tb_mw_tl_font_color"], "#FFFFFF")

    def test_detached_translated_window_textbox_defaults_exist(self) -> None:
        self.assertEqual(default_setting["tb_ex_tl_font"], "Arial")
        self.assertTrue(default_setting["tb_ex_tl_font_bold"])
        self.assertEqual(default_setting["tb_ex_tl_font_size"], 13)
        self.assertEqual(default_setting["tb_ex_tl_font_color"], "#FFFFFF")
        self.assertEqual(default_setting["tb_ex_tl_bg_color"], "#000000")
        self.assertTrue(default_setting["tb_ex_tl_use_conf_color"])


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
