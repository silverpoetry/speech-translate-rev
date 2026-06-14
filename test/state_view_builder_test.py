from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.state_view_builder import StateViewBuilder


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {
            "theme": "dark",
            "log_level": "INFO",
            "dir_export": "auto",
            "dir_model": "auto",
            "export_to": "txt",
            "source_lang_mw": "English",
            "target_lang_mw": "Chinese",
            "input": "mic",
            "tl_engine_mw": "Google Translate",
            "transcribe_mw": True,
            "translate_mw": True,
            "auto_scroll_log": True,
            "auto_refresh_log": False,
            "source_lang_f_import": "English",
            "target_lang_f_import": "Chinese",
            "transcribe_f_import": True,
            "translate_f_import": True,
            "tl_engine_f_import": "Google Translate",
            "model_f_import": "small",
            "selenium_compact_level": 2,
            "selenium_z_order_mode": "behind-main",
            "selenium_auto_close_on_task_done": True,
            "selenium_chrome_user_data_dir": "",
            "enable_initial_prompt": False,
            "initial_prompts_map": {},
            "condition_on_previous_text": True,
            "filter_rec": True,
            "filter_rec_case_sensitive": False,
            "filter_rec_strip": True,
            "filter_rec_ignore_punctuations": "\"',.?!",
            "filter_rec_exact_match": False,
            "filter_rec_similarity": 0.75,
            "filter_file_import": True,
            "filter_file_import_case_sensitive": False,
            "filter_file_import_strip": True,
            "filter_file_import_ignore_punctuations": "\"',.?!",
            "filter_file_import_exact_match": False,
            "filter_file_import_similarity": 0.75,
            "hostAPI": "WASAPI",
            "mic": "Mic 2",
            "speaker": "Speaker 1",
            "verbose_record": True,
            "transcribe_rate": 100,
            "model_device_preference": "auto",
            "separate_with": "\\n",
            "use_temp": False,
            "keep_temp": False,
            "file_use_official_whisper": False,
            "show_audio_visualizer_in_setting": True,
            "sample_rate_mic": 16000,
            "chunk_size_mic": 1024,
            "channels_mic": 1,
            "auto_sample_rate_mic": True,
            "auto_channels_mic": True,
            "min_input_length_mic": 1,
            "max_buffer_mic": 10,
            "max_sentences_mic": 5,
            "mic_no_limit": False,
            "threshold_enable_mic": True,
            "threshold_auto_mic": True,
            "auto_break_buffer_mic": True,
            "threshold_auto_level_mic": 3,
            "threshold_auto_silero_mic": False,
            "threshold_silero_mic_min": 0.5,
            "threshold_db_mic": -40,
            "sample_rate_speaker": 16000,
            "chunk_size_speaker": 1024,
            "channels_speaker": 2,
            "auto_sample_rate_speaker": True,
            "auto_channels_speaker": True,
            "min_input_length_speaker": 1,
            "max_buffer_speaker": 10,
            "max_sentences_speaker": 5,
            "speaker_no_limit": False,
            "threshold_enable_speaker": True,
            "threshold_auto_speaker": True,
            "auto_break_buffer_speaker": True,
            "threshold_auto_level_speaker": 3,
            "threshold_auto_silero_speaker": False,
            "threshold_silero_speaker_min": 0.5,
            "threshold_db_speaker": -40,
        }


class FakeBridge:
    def __init__(self) -> None:
        self.emits = []

    def _build_import_ui(self, verify_available: bool = True):
        return {"verify_available": verify_available}

    def _build_runtime_model_state(self):
        return {"loaded": False, "key": "small"}

    def snapshot_live_state(self):
        return {"main_transcribed_text": "hello"}

    def get_log_file_name(self):
        return "latest.log"

    def get_log_content(self):
        return "content"

    def get_detached_config(self, mode: str):
        return {"mode": mode}

    def _resolve_model_dir(self):
        return "D:/models"

    def _resolve_export_dir(self):
        return "D:/exports"

    def _emit_ui_update(self, sections):
        self.emits.append(tuple(sections))


class StateViewBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.settings = FakeSettings()
        self.builder = StateViewBuilder(self.bridge, self.settings)

    def test_build_audio_source_options_falls_back_to_default_entries(self) -> None:
        self.builder.audio_source_cache = {
            "host_api_options": ["WASAPI", "MME"],
            "mic_options_by_host": {"WASAPI": ["Mic 1", "Mic 2"]},
            "speaker_options_by_host": {"WASAPI": ["Speaker 1"]},
            "mic_options_all": ["Mic 1", "Mic 2"],
            "speaker_options_all": ["Speaker 1"],
            "default_host_api": "WASAPI",
            "default_mic": "Mic 1",
            "default_speaker": "Speaker 1",
        }

        options = self.builder.build_audio_source_options(host_api="Invalid")

        self.assertEqual(options["selected_host_api"], "WASAPI")
        self.assertEqual(options["selected_mic"], "Mic 2")
        self.assertEqual(options["selected_speaker"], "Speaker 1")

    def test_build_state_includes_detached_config_and_runtime_state(self) -> None:
        self.builder.audio_source_cache = {
            "host_api_options": [],
            "mic_options_by_host": {},
            "speaker_options_by_host": {},
            "mic_options_all": [],
            "speaker_options_all": [],
            "default_host_api": "",
            "default_mic": "",
            "default_speaker": "",
        }

        state = self.builder.build_state()

        self.assertEqual(state["app_name"], "Speech Translate")
        self.assertEqual(state["runtime_model"]["key"], "small")
        self.assertEqual(state["import_ui"]["verify_available"], False)
        self.assertEqual(state["detached_config"]["tc"]["mode"], "tc")
        self.assertEqual(state["about"]["model_dir"], "D:/models")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
