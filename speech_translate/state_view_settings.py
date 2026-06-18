from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, cast

from speech_translate.controller_protocols import JsonDict


@dataclass(frozen=True)
class RecordDeviceViewSettings:
    sample_rate: object
    chunk_size: object
    channels: object
    auto_sample_rate: object
    auto_channels: object
    min_input: object
    max_buffer: object
    max_sentences: object
    no_limit: object
    threshold_enable: object
    threshold_auto: object
    auto_break_buffer: object
    threshold_auto_level: object
    threshold_auto_silero: object
    threshold_silero_min: object
    threshold_db: object

    def to_payload(self) -> JsonDict:
        return {
            "sample_rate": self.sample_rate,
            "chunk_size": self.chunk_size,
            "channels": self.channels,
            "auto_sample_rate": self.auto_sample_rate,
            "auto_channels": self.auto_channels,
            "min_input": self.min_input,
            "max_buffer": self.max_buffer,
            "max_sentences": self.max_sentences,
            "no_limit": self.no_limit,
            "threshold_enable": self.threshold_enable,
            "threshold_auto": self.threshold_auto,
            "auto_break_buffer": self.auto_break_buffer,
            "threshold_auto_level": self.threshold_auto_level,
            "threshold_auto_silero": self.threshold_auto_silero,
            "threshold_silero_min": self.threshold_silero_min,
            "threshold_db": self.threshold_db,
        }


@dataclass(frozen=True)
class MainViewSettings:
    selected_input: object
    selected_model: object
    selected_backend: str
    selected_source: object
    selected_target: object
    selected_engine: object
    transcribe: bool
    translate: bool
    auto_scroll_log: object
    auto_refresh_log: object

    def to_payload(self) -> JsonDict:
        return {
            "selected_input": self.selected_input,
            "selected_model": self.selected_model,
            "selected_backend": self.selected_backend,
            "selected_source": self.selected_source,
            "selected_target": self.selected_target,
            "selected_engine": self.selected_engine,
            "transcribe": self.transcribe,
            "translate": self.translate,
            "auto_scroll_log": self.auto_scroll_log,
            "auto_refresh_log": self.auto_refresh_log,
        }


@dataclass(frozen=True)
class RecordViewSettings:
    input: object
    host_api: object
    mic: object
    speaker: object
    verbose_record: object
    transcribe_rate: object
    model_device_preference: str
    separate_with: object
    use_temp: object
    keep_temp: object
    file_use_official_whisper: bool
    mic_device: RecordDeviceViewSettings
    speaker_device: RecordDeviceViewSettings

    def to_payload(self, *, audio_sources: JsonDict) -> JsonDict:
        return {
            "input": self.input,
            "host_api": self.host_api,
            "mic": self.mic,
            "speaker": self.speaker,
            "host_api_options": audio_sources.get("host_api_options", []),
            "mic_options": audio_sources.get("mic_options", []),
            "speaker_options": audio_sources.get("speaker_options", []),
            "verbose_record": self.verbose_record,
            "transcribe_rate": self.transcribe_rate,
            "model_device_preference": self.model_device_preference,
            "model_device_options": ["auto", "cpu", "cuda"],
            "separate_with": self.separate_with,
            "use_temp": self.use_temp,
            "keep_temp": self.keep_temp,
            "file_use_official_whisper": self.file_use_official_whisper,
            "mic_device": self.mic_device.to_payload(),
            "speaker_device": self.speaker_device.to_payload(),
        }


@dataclass(frozen=True)
class CompactViewSettings:
    payload: JsonDict = field(default_factory=dict)

    def to_payload(self) -> JsonDict:
        return dict(self.payload)


@dataclass(frozen=True)
class StateViewSettingsBundle:
    snapshot: dict[str, object]
    log_level: str
    main_ui: MainViewSettings
    record_ui: RecordViewSettings
    compact_settings: CompactViewSettings


def _copy_settings_snapshot(settings_snapshot: Mapping[str, object]) -> dict[str, object]:
    return cast(dict[str, object], dict(settings_snapshot))


def build_record_device_view_settings(settings_snapshot: Mapping[str, object], device: str) -> RecordDeviceViewSettings:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    return RecordDeviceViewSettings(
        sample_rate=snapshot.get(f"sample_rate_{device}"),
        chunk_size=snapshot.get(f"chunk_size_{device}"),
        channels=snapshot.get(f"channels_{device}"),
        auto_sample_rate=snapshot.get(f"auto_sample_rate_{device}"),
        auto_channels=snapshot.get(f"auto_channels_{device}"),
        min_input=snapshot.get(f"min_input_length_{device}"),
        max_buffer=snapshot.get(f"max_buffer_{device}"),
        max_sentences=snapshot.get(f"max_sentences_{device}"),
        no_limit=snapshot.get(f"{device}_no_limit"),
        threshold_enable=snapshot.get(f"threshold_enable_{device}"),
        threshold_auto=snapshot.get(f"threshold_auto_{device}"),
        auto_break_buffer=snapshot.get(f"auto_break_buffer_{device}"),
        threshold_auto_level=snapshot.get(f"threshold_auto_level_{device}"),
        threshold_auto_silero=snapshot.get(f"threshold_auto_silero_{device}"),
        threshold_silero_min=snapshot.get(f"threshold_silero_{device}_min"),
        threshold_db=snapshot.get(f"threshold_db_{device}"),
    )


def build_state_view_settings(settings_snapshot: Mapping[str, object]) -> StateViewSettingsBundle:
    snapshot = _copy_settings_snapshot(settings_snapshot)
    return StateViewSettingsBundle(
        snapshot=snapshot,
        log_level=str(snapshot.get("log_level", "DEBUG")),
        main_ui=MainViewSettings(
            selected_input=snapshot.get("input"),
            selected_model=snapshot.get("model_mw"),
            selected_backend="faster-whisper" if bool(snapshot.get("use_faster_whisper", True)) else "whisper",
            selected_source=snapshot.get("source_lang_mw"),
            selected_target=snapshot.get("target_lang_mw"),
            selected_engine=snapshot.get("tl_engine_mw"),
            transcribe=bool(snapshot.get("transcribe_mw", True)),
            translate=bool(snapshot.get("translate_mw", True)),
            auto_scroll_log=snapshot.get("auto_scroll_log"),
            auto_refresh_log=snapshot.get("auto_refresh_log"),
        ),
        record_ui=RecordViewSettings(
            input=snapshot.get("input"),
            host_api=snapshot.get("hostAPI"),
            mic=snapshot.get("mic"),
            speaker=snapshot.get("speaker"),
            verbose_record=snapshot.get("verbose_record"),
            transcribe_rate=snapshot.get("transcribe_rate"),
            model_device_preference=str(snapshot.get("model_device_preference", "auto")),
            separate_with=snapshot.get("separate_with"),
            use_temp=snapshot.get("use_temp"),
            keep_temp=snapshot.get("keep_temp"),
            file_use_official_whisper=bool(snapshot.get("file_use_official_whisper", False)),
            mic_device=build_record_device_view_settings(snapshot, "mic"),
            speaker_device=build_record_device_view_settings(snapshot, "speaker"),
        ),
        compact_settings=CompactViewSettings(
            {
                "log_level": snapshot.get("log_level"),
                "dir_export": snapshot.get("dir_export"),
                "dir_model": snapshot.get("dir_model"),
                "dir_log": snapshot.get("dir_log"),
                "export_to": snapshot.get("export_to"),
                "source_lang_mw": snapshot.get("source_lang_mw"),
                "target_lang_mw": snapshot.get("target_lang_mw"),
                "input": snapshot.get("input"),
                "model_mw": snapshot.get("model_mw"),
                "tl_engine_mw": snapshot.get("tl_engine_mw"),
                "transcribe_mw": bool(snapshot.get("transcribe_mw", True)),
                "translate_mw": bool(snapshot.get("translate_mw", True)),
                "hostAPI": snapshot.get("hostAPI"),
                "mic": snapshot.get("mic"),
                "speaker": snapshot.get("speaker"),
                "auto_scroll_log": snapshot.get("auto_scroll_log"),
                "auto_refresh_log": snapshot.get("auto_refresh_log"),
                "source_lang_f_import": snapshot.get("source_lang_f_import"),
                "target_lang_f_import": snapshot.get("target_lang_f_import"),
                "transcribe_f_import": snapshot.get("transcribe_f_import"),
                "translate_f_import": snapshot.get("translate_f_import"),
                "tl_engine_f_import": snapshot.get("tl_engine_f_import"),
                "model_f_import": snapshot.get("model_f_import"),
                "selenium_compact_level": snapshot.get("selenium_compact_level", 2),
                "selenium_z_order_mode": snapshot.get("selenium_z_order_mode", "behind-main"),
                "selenium_auto_close_on_task_done": snapshot.get("selenium_auto_close_on_task_done", True),
                "selenium_chrome_user_data_dir": snapshot.get("selenium_chrome_user_data_dir", ""),
                "enable_initial_prompt": snapshot.get("enable_initial_prompt", False),
                "initial_prompts_map": snapshot.get("initial_prompts_map", {}),
                "condition_on_previous_text": snapshot.get("condition_on_previous_text", True),
                "filter_rec": snapshot.get("filter_rec", True),
                "filter_rec_case_sensitive": snapshot.get("filter_rec_case_sensitive", False),
                "filter_rec_strip": snapshot.get("filter_rec_strip", True),
                "filter_rec_ignore_punctuations": snapshot.get("filter_rec_ignore_punctuations", "\"',.?!"),
                "filter_rec_exact_match": snapshot.get("filter_rec_exact_match", False),
                "filter_rec_similarity": snapshot.get("filter_rec_similarity", 0.75),
                "filter_file_import": snapshot.get("filter_file_import", True),
                "filter_file_import_case_sensitive": snapshot.get("filter_file_import_case_sensitive", False),
                "filter_file_import_strip": snapshot.get("filter_file_import_strip", True),
                "filter_file_import_ignore_punctuations": snapshot.get("filter_file_import_ignore_punctuations", "\"',.?!"),
                "filter_file_import_exact_match": snapshot.get("filter_file_import_exact_match", False),
                "filter_file_import_similarity": snapshot.get("filter_file_import_similarity", 0.75),
                "http_proxy_enable": snapshot.get("http_proxy_enable", False),
                "http_proxy": snapshot.get("http_proxy", ""),
                "https_proxy_enable": snapshot.get("https_proxy_enable", False),
                "https_proxy": snapshot.get("https_proxy", ""),
                "libre_link": snapshot.get("libre_link", ""),
                "libre_api_key": snapshot.get("libre_api_key", ""),
                "auto_open_dir_export": snapshot.get("auto_open_dir_export", True),
                "export_format": snapshot.get("export_format", ""),
                "export_to": snapshot.get("export_to", []),
                "model_device_preference": snapshot.get("model_device_preference", "auto"),
                "transcribe_rate": snapshot.get("transcribe_rate", 300),
                "separate_with": snapshot.get("separate_with", "\\n"),
                "use_temp": snapshot.get("use_temp", False),
                "keep_temp": snapshot.get("keep_temp", False),
                "file_use_official_whisper": snapshot.get("file_use_official_whisper", False),
                "path_filter_rec": snapshot.get("path_filter_rec", "auto"),
                "path_filter_file_import": snapshot.get("path_filter_file_import", "auto"),
                "remove_repetition_file_import": snapshot.get("remove_repetition_file_import", False),
                "remove_repetition_amount": snapshot.get("remove_repetition_amount", 1),
                "segment_max_words": snapshot.get("segment_max_words", ""),
                "segment_max_chars": snapshot.get("segment_max_chars", ""),
                "segment_split_or_newline": snapshot.get("segment_split_or_newline", "Split"),
                "segment_even_split": snapshot.get("segment_even_split", True),
                "segment_level": snapshot.get("segment_level", True),
                "word_level": snapshot.get("word_level", True),
                "use_faster_whisper": snapshot.get("use_faster_whisper", True),
                "use_en_model": snapshot.get("use_en_model", True),
                "decoding_preset": snapshot.get("decoding_preset", "beam search"),
                "temperature": snapshot.get("temperature", "0.0, 0.2, 0.4, 0.6, 0.8, 1.0"),
                "best_of": snapshot.get("best_of", 3),
                "beam_size": snapshot.get("beam_size", 3),
                "patience": snapshot.get("patience", 1.0),
                "compression_ratio_threshold": snapshot.get("compression_ratio_threshold", 2.4),
                "logprob_threshold": snapshot.get("logprob_threshold", -1.0),
                "no_speech_threshold": snapshot.get("no_speech_threshold", 0.72),
                "suppress_tokens": snapshot.get("suppress_tokens", ""),
                "suppress_blank": snapshot.get("suppress_blank", True),
                "fp16": snapshot.get("fp16", True),
                "initial_prompt": snapshot.get("initial_prompt", None),
                "prefix": snapshot.get("prefix", None),
                "max_initial_timestamp": snapshot.get("max_initial_timestamp", 1.0),
                "whisper_args": snapshot.get("whisper_args", ""),
                "file_slice_start": snapshot.get("file_slice_start", ""),
                "file_slice_end": snapshot.get("file_slice_end", ""),
                "auto_open_dir_translate": snapshot.get("auto_open_dir_translate", True),
                "auto_open_dir_refinement": snapshot.get("auto_open_dir_refinement", True),
                "auto_open_dir_alignment": snapshot.get("auto_open_dir_alignment", True),
                "debug_realtime_record": snapshot.get("debug_realtime_record", False),
                "debug_translate": snapshot.get("debug_translate", False),
                "rec_ask_confirmation_first": snapshot.get("rec_ask_confirmation_first", False),
                "close_to_tray_on_close": snapshot.get("close_to_tray_on_close", True),
                "supress_hidden_to_tray": snapshot.get("supress_hidden_to_tray", False),
                "supress_record_warning": snapshot.get("supress_record_warning", False),
                "colorize_per_segment": snapshot.get("colorize_per_segment", True),
                "colorize_per_word": snapshot.get("colorize_per_word", False),
                "gradient_low_conf": snapshot.get("gradient_low_conf", "#FF0000"),
                "gradient_high_conf": snapshot.get("gradient_high_conf", "#00FF00"),
                "tb_mw_tc_auto_scroll": snapshot.get("tb_mw_tc_auto_scroll", True),
                "tb_mw_tc_limit_max": snapshot.get("tb_mw_tc_limit_max", False),
                "tb_mw_tc_limit_max_per_line": snapshot.get("tb_mw_tc_limit_max_per_line", False),
                "tb_mw_tc_max": snapshot.get("tb_mw_tc_max", 300),
                "tb_mw_tc_max_per_line": snapshot.get("tb_mw_tc_max_per_line", 30),
                "tb_mw_tc_font": snapshot.get("tb_mw_tc_font", "TKDefaultFont"),
                "tb_mw_tc_font_bold": snapshot.get("tb_mw_tc_font_bold", False),
                "tb_mw_tc_font_size": snapshot.get("tb_mw_tc_font_size", 10),
                "tb_mw_tc_font_color": snapshot.get("tb_mw_tc_font_color", "#FFFFFF"),
                "tb_mw_tc_use_conf_color": snapshot.get("tb_mw_tc_use_conf_color", True),
                "tb_mw_tl_auto_scroll": snapshot.get("tb_mw_tl_auto_scroll", True),
                "tb_mw_tl_limit_max": snapshot.get("tb_mw_tl_limit_max", False),
                "tb_mw_tl_limit_max_per_line": snapshot.get("tb_mw_tl_limit_max_per_line", False),
                "tb_mw_tl_max": snapshot.get("tb_mw_tl_max", 300),
                "tb_mw_tl_max_per_line": snapshot.get("tb_mw_tl_max_per_line", 30),
                "tb_mw_tl_font": snapshot.get("tb_mw_tl_font", "TKDefaultFont"),
                "tb_mw_tl_font_bold": snapshot.get("tb_mw_tl_font_bold", False),
                "tb_mw_tl_font_size": snapshot.get("tb_mw_tl_font_size", 10),
                "tb_mw_tl_font_color": snapshot.get("tb_mw_tl_font_color", "#FFFFFF"),
                "tb_mw_tl_use_conf_color": snapshot.get("tb_mw_tl_use_conf_color", True),
            }
        ),
    )


__all__ = [
    "CompactViewSettings",
    "MainViewSettings",
    "RecordDeviceViewSettings",
    "RecordViewSettings",
    "StateViewSettingsBundle",
    "build_record_device_view_settings",
    "build_state_view_settings",
]
