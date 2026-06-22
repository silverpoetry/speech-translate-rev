from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate._constants import APP_NAME
from speech_translate.state_view_builder import StateViewBuilder, StateViewDependencies
from speech_translate.state_view_settings import build_record_device_view_settings, build_state_view_settings
from speech_translate.ui_protocol import UI_SECTION_STATE


class FakeSettings:
    def __init__(self) -> None:
        self.cache = {
            "log_level": "INFO",
            "dir_export": "auto",
            "dir_model": "auto",
            "dir_log": "auto",
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
            "model_mw": "tiny",
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
            "http_proxy_enable": True,
            "http_proxy": "http://127.0.0.1:7890",
            "https_proxy_enable": True,
            "https_proxy": "http://127.0.0.1:7890",
            "libre_link": "https://libretranslate.example.com",
            "libre_api_key": "secret-key",
            "auto_open_dir_export": True,
            "export_format": "%Y-%m-%d %f {file}/{task-lang}",
            "path_filter_rec": "D:/filters/record.json",
            "path_filter_file_import": "D:/filters/file.json",
            "remove_repetition_file_import": True,
            "remove_repetition_amount": 2,
            "segment_max_words": "12",
            "segment_max_chars": "80",
            "segment_split_or_newline": "Split",
            "segment_even_split": True,
            "segment_level": True,
            "word_level": False,
            "use_en_model": True,
            "decoding_preset": "beam search",
            "temperature": "0.0, 0.2",
            "best_of": 3,
            "beam_size": 3,
            "patience": 1.0,
            "compression_ratio_threshold": 2.4,
            "logprob_threshold": -1.0,
            "no_speech_threshold": 0.72,
            "suppress_tokens": "-1",
            "suppress_blank": False,
            "fp16": True,
            "initial_prompt": "custom prompt",
            "prefix": "prefix text",
            "max_initial_timestamp": 1.0,
            "whisper_args": "--vad True",
            "file_slice_start": "5",
            "file_slice_end": "120",
            "auto_open_dir_translate": True,
            "auto_open_dir_refinement": False,
            "auto_open_dir_alignment": True,
            "debug_realtime_record": True,
            "debug_translate": True,
            "rec_ask_confirmation_first": False,
            "supress_hidden_to_tray": True,
            "supress_record_warning": False,
            "colorize_per_segment": True,
            "colorize_per_word": False,
            "gradient_low_conf": "#112233",
            "gradient_high_conf": "#aabbcc",
            "ex_tc_geometry": "980x280",
            "ex_tc_pos": "100,120",
            "ex_tc_always_on_top": True,
            "ex_tc_no_title_bar": True,
            "ex_tc_click_through": False,
            "ex_tc_opacity": 0.92,
            "tb_ex_tc_font": "Segoe UI",
            "tb_ex_tc_font_bold": True,
            "tb_ex_tc_font_size": 16,
            "tb_ex_tc_font_color": "#ffffff",
            "tb_ex_tc_bg_color": "#101820",
            "tb_ex_tc_limit_max": True,
            "tb_ex_tc_limit_max_per_line": False,
            "tb_ex_tc_max": 80,
            "tb_ex_tc_max_per_line": 24,
            "tb_ex_tc_use_conf_color": True,
            "ex_tl_geometry": "760x220",
            "ex_tl_pos": "200,160",
            "ex_tl_always_on_top": False,
            "ex_tl_no_title_bar": True,
            "ex_tl_click_through": True,
            "ex_tl_opacity": 0.88,
            "tb_ex_tl_font": "Consolas",
            "tb_ex_tl_font_bold": False,
            "tb_ex_tl_font_size": 15,
            "tb_ex_tl_font_color": "#ddeeff",
            "tb_ex_tl_bg_color": "#0b0f18",
            "tb_ex_tl_limit_max": False,
            "tb_ex_tl_limit_max_per_line": True,
            "tb_ex_tl_max": 96,
            "tb_ex_tl_max_per_line": 28,
            "tb_ex_tl_use_conf_color": False,
            "tb_mw_tc_auto_scroll": True,
            "tb_mw_tc_limit_max": True,
            "tb_mw_tc_limit_max_per_line": False,
            "tb_mw_tc_max": 400,
            "tb_mw_tc_max_per_line": 33,
            "tb_mw_tc_font": "Arial",
            "tb_mw_tc_font_bold": True,
            "tb_mw_tc_font_size": 14,
            "tb_mw_tc_font_color": "#ddeeff",
            "tb_mw_tc_use_conf_color": True,
            "tb_mw_tl_auto_scroll": False,
            "tb_mw_tl_limit_max": True,
            "tb_mw_tl_limit_max_per_line": True,
            "tb_mw_tl_max": 500,
            "tb_mw_tl_max_per_line": 44,
            "tb_mw_tl_font": "Consolas",
            "tb_mw_tl_font_bold": False,
            "tb_mw_tl_font_size": 15,
            "tb_mw_tl_font_color": "#ccbbaa",
            "tb_mw_tl_use_conf_color": False,
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


class FakeImportQueueController:
    def build_import_ui(self, verify_available: bool = True):
        return {"verify_available": verify_available}


class FakeModelManagerController:
    def build_runtime_model_state(self):
        return {"loaded": False, "key": "small"}

    def resolve_model_dir(self):
        return "D:/models"

    def get_model_manager_keys(self):
        return ["tiny", "small", "medium"]

    def normalize_model_key(self, value: str):
        return {"⛵ Small [2GB VRAM] (Moderate)": "small"}.get(value, value)


class FakeSystemSettingsController:
    def get_log_file_name(self):
        return "latest.log"

    def get_log_content(self):
        return "content"

    def resolve_log_dir(self):
        return "D:/logs"

    def resolve_export_dir(self):
        return "D:/exports"


class FakeDetachedWindowController:
    def get_detached_config(self, mode: str):
        return {"mode": mode}


class FakeStateViewCallbacks:
    def __init__(self) -> None:
        self.emits = []

    def snapshot_live_state(self):
        return {"main_transcribed_text": "hello"}

    def emit_ui_update(self, sections):
        self.emits.append(tuple(sections))


class StateViewBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.callbacks = FakeStateViewCallbacks()
        self.settings = FakeSettings()
        self.builder = StateViewBuilder(
            StateViewDependencies(
                import_queue_controller=FakeImportQueueController(),
                model_manager_controller=FakeModelManagerController(),
                system_settings_controller=FakeSystemSettingsController(),
                detached_window_controller=FakeDetachedWindowController(),
                snapshot_live_state=self.callbacks.snapshot_live_state,
                emit_ui_update=self.callbacks.emit_ui_update,
            ),
            self.settings,
        )

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

        self.assertEqual(state["app_name"], APP_NAME)
        self.assertEqual(state["runtime_model"]["key"], "small")
        self.assertEqual(state["import_ui"]["verify_available"], False)
        self.assertEqual(state["main_ui"]["selected_model"], "tiny")
        self.assertEqual(state["main_ui"]["selected_backend"], "faster-whisper")
        self.assertEqual(state["main_ui"]["backend_options"], ["whisper", "faster-whisper"])
        self.assertEqual(state["main_ui"]["model_options"], ["tiny", "small", "medium"])
        self.assertEqual(state["detached_config"]["tc"]["mode"], "tc")
        self.assertEqual(state["about"]["model_dir"], "D:/models")
        self.assertEqual(state["about"]["log_dir"], "D:/logs")

    def test_build_state_view_settings_extracts_view_payloads(self) -> None:
        view_settings = build_state_view_settings(self.settings.cache)
        compact = view_settings.compact_settings.to_payload()

        self.assertEqual(view_settings.log_level, "INFO")
        self.assertEqual(view_settings.main_ui.selected_input, "mic")
        self.assertEqual(view_settings.main_ui.selected_model, self.settings.cache["model_mw"])
        self.assertEqual(view_settings.main_ui.selected_backend, "faster-whisper")
        self.assertEqual(view_settings.main_ui.selected_engine, "Google Translate")
        self.assertEqual(view_settings.record_ui.model_device_preference, "auto")
        self.assertEqual(compact["model_mw"], self.settings.cache["model_mw"])
        self.assertEqual(compact["model_f_import"], "small")
        self.assertTrue(compact["use_faster_whisper"])
        self.assertEqual(compact["http_proxy"], "http://127.0.0.1:7890")
        self.assertEqual(compact["libre_link"], "https://libretranslate.example.com")
        self.assertEqual(compact["segment_max_words"], "12")
        self.assertEqual(compact["decoding_preset"], "beam search")
        self.assertEqual(compact["whisper_args"], "--vad True")
        self.assertEqual(compact["dir_log"], "auto")
        self.assertEqual(compact["path_filter_rec"], "D:/filters/record.json")
        self.assertEqual(compact["path_filter_file_import"], "D:/filters/file.json")
        self.assertEqual(compact["initial_prompt"], "custom prompt")
        self.assertEqual(compact["prefix"], "prefix text")
        self.assertFalse(compact["suppress_blank"])
        self.assertEqual(compact["file_slice_start"], "5")
        self.assertFalse(compact["auto_open_dir_refinement"])
        self.assertEqual(compact["gradient_low_conf"], "#112233")
        self.assertEqual(compact["ex_tc_geometry"], "980x280")
        self.assertEqual(compact["ex_tc_pos"], "100,120")
        self.assertEqual(compact["tb_ex_tc_font"], "Segoe UI")
        self.assertEqual(compact["tb_ex_tc_max"], 80)
        self.assertEqual(compact["ex_tl_geometry"], "760x220")
        self.assertEqual(compact["ex_tl_click_through"], True)
        self.assertEqual(compact["tb_ex_tl_bg_color"], "#0b0f18")
        self.assertEqual(compact["tb_mw_tc_font"], "Arial")
        self.assertEqual(compact["tb_mw_tc_font_color"], "#ddeeff")
        self.assertEqual(compact["tb_mw_tl_font"], "Consolas")
        self.assertEqual(compact["tb_mw_tl_font_color"], "#ccbbaa")

    def test_build_state_view_settings_normalizes_legacy_model_display_values(self) -> None:
        self.settings.cache["model_mw"] = "⚡ Tiny [1GB VRAM] (Fastest)"
        self.settings.cache["model_f_import"] = "⛵ Small [2GB VRAM] (Moderate)"

        view_settings = build_state_view_settings(self.settings.cache)
        compact = view_settings.compact_settings.to_payload()

        self.assertEqual(view_settings.main_ui.selected_model, "tiny")
        self.assertEqual(compact["model_mw"], "tiny")
        self.assertEqual(compact["model_f_import"], "small")

    def test_build_record_device_view_settings_extracts_device_thresholds(self) -> None:
        device_settings = build_record_device_view_settings(self.settings.cache, "speaker")

        self.assertEqual(device_settings.sample_rate, 16000)
        self.assertEqual(device_settings.channels, 2)
        self.assertTrue(device_settings.threshold_enable)
        self.assertEqual(device_settings.threshold_db, -40)

    def test_prime_audio_source_cache_falls_back_to_error_entries_on_failure(self) -> None:
        with patch("speech_translate.state_view_builder.get_host_apis", side_effect=RuntimeError("boom")):
            self.builder.prime_audio_source_cache()

        self.assertTrue(self.builder.audio_source_cache_ready)
        self.assertFalse(self.builder.audio_source_cache_loading)
        self.assertEqual(self.builder.audio_source_cache["mic_options_all"], ["[ERROR] Failed to load input devices"])
        self.assertEqual(self.builder.audio_source_cache["speaker_options_all"], ["[ERROR] Failed to load output devices"])
        self.assertIn((UI_SECTION_STATE,), self.callbacks.emits)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
