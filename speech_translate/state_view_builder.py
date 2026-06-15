from __future__ import annotations

from dataclasses import asdict, dataclass
from platform import processor, release, system, version
from threading import Thread
from typing import Dict, Optional

from speech_translate._constants import APP_NAME
from speech_translate._version import __version__
from speech_translate.controller_protocols import JsonDict, SettingsStore, StateViewBridge
from speech_translate.log_helpers import logger
from speech_translate.ui_protocol import UI_SECTION_STATE
from speech_translate.utils.audio.device import (
    get_default_host_api,
    get_default_input_device,
    get_default_output_device,
    get_host_apis,
    get_input_devices,
    get_output_devices,
)
from speech_translate.utils.translate.language import WHISPER_LANG_LIST


@dataclass
class AppState:
    app_name: str
    version: str
    os_name: str
    os_release: str
    os_version: str
    cpu: str
    settings: JsonDict
    import_ui: JsonDict
    main_ui: JsonDict
    record_ui: JsonDict
    runtime_model: JsonDict
    live_ui: JsonDict
    about: JsonDict
    log_level: str
    current_log: str
    log_content: str


class StateViewBuilder:
    """Builds UI-facing state snapshots and manages audio source discovery cache."""

    def __init__(self, bridge: StateViewBridge, settings: SettingsStore):
        self.bridge = bridge
        self.settings = settings
        self.audio_source_cache: JsonDict = self._empty_audio_source_cache()
        self.audio_source_cache_ready = False
        self.audio_source_cache_loading = True

    def start_audio_source_scan(self) -> None:
        Thread(target=self.prime_audio_source_cache, daemon=True).start()

    def _empty_audio_source_cache(self) -> JsonDict:
        return {
            "host_api_options": [],
            "mic_options_by_host": {},
            "speaker_options_by_host": {},
            "mic_options_all": [],
            "speaker_options_all": [],
            "default_host_api": "",
            "default_mic": "",
            "default_speaker": "",
        }

    def _settings_snapshot(self) -> dict[str, object]:
        return dict(self.settings.cache)

    def _build_system_state(self) -> AppState:
        settings_snapshot = self._settings_snapshot()
        return AppState(
            app_name=APP_NAME,
            version=__version__,
            os_name=system(),
            os_release=release(),
            os_version=version(),
            cpu=processor(),
            settings=self._build_compact_settings(settings_snapshot),
            import_ui=self.bridge.build_import_ui(verify_available=False),
            main_ui=self.build_main_ui(),
            record_ui=self.build_record_ui(),
            runtime_model=self.bridge.build_runtime_model_state(),
            live_ui=self.bridge.snapshot_live_state(),
            about=self.build_about(),
            log_level=settings_snapshot.get("log_level", "DEBUG"),
            current_log=self.bridge.get_log_file_name(),
            log_content=self.bridge.get_log_content(),
        )

    def _build_detached_config(self) -> JsonDict:
        return {
            "tc": self.bridge.get_detached_config("tc"),
            "tl": self.bridge.get_detached_config("tl"),
        }

    def build_state(self) -> JsonDict:
        result = asdict(self._build_system_state())
        result["detached_config"] = self._build_detached_config()
        return result

    def reload_state(self) -> JsonDict:
        return self.build_state()

    def build_main_ui(self) -> JsonDict:
        settings_snapshot = self._settings_snapshot()
        return {
            "input_options": ["mic", "speaker"],
            "source_options": WHISPER_LANG_LIST,
            "target_options": WHISPER_LANG_LIST,
            "engine_options": [
                "Selenium Chrome Translate",
                "Google Translate",
                "MyMemoryTranslator",
                "LibreTranslate",
            ],
            "selected_input": settings_snapshot.get("input"),
            "selected_source": settings_snapshot.get("source_lang_mw"),
            "selected_target": settings_snapshot.get("target_lang_mw"),
            "selected_engine": settings_snapshot.get("tl_engine_mw"),
            "transcribe": settings_snapshot.get("transcribe_mw", True),
            "translate": settings_snapshot.get("translate_mw", True),
            "auto_scroll_log": settings_snapshot.get("auto_scroll_log"),
            "auto_refresh_log": settings_snapshot.get("auto_refresh_log"),
        }

    def build_record_device_ui(self, device: str) -> JsonDict:
        settings_snapshot = self._settings_snapshot()
        return {
            "sample_rate": settings_snapshot.get(f"sample_rate_{device}"),
            "chunk_size": settings_snapshot.get(f"chunk_size_{device}"),
            "channels": settings_snapshot.get(f"channels_{device}"),
            "auto_sample_rate": settings_snapshot.get(f"auto_sample_rate_{device}"),
            "auto_channels": settings_snapshot.get(f"auto_channels_{device}"),
            "min_input": settings_snapshot.get(f"min_input_length_{device}"),
            "max_buffer": settings_snapshot.get(f"max_buffer_{device}"),
            "max_sentences": settings_snapshot.get(f"max_sentences_{device}"),
            "no_limit": settings_snapshot.get(f"{device}_no_limit"),
            "threshold_enable": settings_snapshot.get(f"threshold_enable_{device}"),
            "threshold_auto": settings_snapshot.get(f"threshold_auto_{device}"),
            "auto_break_buffer": settings_snapshot.get(f"auto_break_buffer_{device}"),
            "threshold_auto_level": settings_snapshot.get(f"threshold_auto_level_{device}"),
            "threshold_auto_silero": settings_snapshot.get(f"threshold_auto_silero_{device}"),
            "threshold_silero_min": settings_snapshot.get(f"threshold_silero_{device}_min"),
            "threshold_db": settings_snapshot.get(f"threshold_db_{device}"),
        }

    def build_record_ui(self) -> JsonDict:
        settings_snapshot = self._settings_snapshot()
        audio_sources = self.build_audio_source_options()
        return {
            "input": settings_snapshot.get("input"),
            "host_api": settings_snapshot.get("hostAPI"),
            "mic": settings_snapshot.get("mic"),
            "speaker": settings_snapshot.get("speaker"),
            "host_api_options": audio_sources.get("host_api_options", []),
            "mic_options": audio_sources.get("mic_options", []),
            "speaker_options": audio_sources.get("speaker_options", []),
            "verbose_record": settings_snapshot.get("verbose_record"),
            "transcribe_rate": settings_snapshot.get("transcribe_rate"),
            "model_device_preference": settings_snapshot.get("model_device_preference", "auto"),
            "model_device_options": ["auto", "cpu", "cuda"],
            "separate_with": settings_snapshot.get("separate_with"),
            "use_temp": settings_snapshot.get("use_temp"),
            "keep_temp": settings_snapshot.get("keep_temp"),
            "file_use_official_whisper": settings_snapshot.get("file_use_official_whisper", False),
            "show_audio_visualizer_in_setting": settings_snapshot.get("show_audio_visualizer_in_setting"),
            "mic_device": self.build_record_device_ui("mic"),
            "speaker_device": self.build_record_device_ui("speaker"),
        }

    def build_about(self) -> JsonDict:
        return {
            "name": APP_NAME,
            "version": __version__,
            "os": f"{system()} {release()} {version()}",
            "cpu": processor(),
            "log_file": self.bridge.get_log_file_name(),
            "model_dir": self.bridge.resolve_model_dir(),
            "export_dir": self.bridge.resolve_export_dir(),
        }

    def _find_default_device(self, device_info: object, all_options: list[object]) -> str:
        if not device_info or not isinstance(device_info, dict):
            return ""
        name = str(device_info.get("name", ""))
        if not name:
            return ""
        return next(
            (
                str(item)
                for item in all_options
                if isinstance(item, str) and "[ID:" in item and name.lower() in item.lower()
            ),
            "",
        )

    def _build_audio_source_cache(self) -> JsonDict:
        host_api_options = get_host_apis()
        mic_options_all = get_input_devices("")
        speaker_options_all = get_output_devices("")
        ok_host, host_info = get_default_host_api()
        default_host_api = str(host_info.get("name", "")) if ok_host and isinstance(host_info, dict) else ""

        mic_options_by_host: dict[str, object] = {}
        speaker_options_by_host: dict[str, object] = {}
        for host_api in host_api_options:
            if isinstance(host_api, str) and not host_api.startswith("["):
                mic_options_by_host[host_api] = get_input_devices(str(host_api))
                speaker_options_by_host[host_api] = get_output_devices(str(host_api))

        return {
            "host_api_options": host_api_options,
            "mic_options_by_host": mic_options_by_host,
            "speaker_options_by_host": speaker_options_by_host,
            "mic_options_all": mic_options_all,
            "speaker_options_all": speaker_options_all,
            "default_host_api": default_host_api,
            "default_mic": self._find_default_device(get_default_input_device()[1], mic_options_all),
            "default_speaker": self._find_default_device(get_default_output_device()[1], speaker_options_all),
        }

    def prime_audio_source_cache(self) -> None:
        try:
            self.audio_source_cache = self._build_audio_source_cache()
        except Exception as exc:
            logger.exception(exc)
            self.audio_source_cache = {
                **self._empty_audio_source_cache(),
                "mic_options_all": ["[ERROR] Failed to load input devices"],
                "speaker_options_all": ["[ERROR] Failed to load output devices"],
            }
        finally:
            self.audio_source_cache_loading = False
            self.audio_source_cache_ready = True
            try:
                self.bridge.emit_ui_update([UI_SECTION_STATE])
            except Exception:
                pass

    def build_audio_source_options(self, selected_host_api: Optional[str] = None, host_api: Optional[str] = None) -> JsonDict:
        settings_snapshot = self._settings_snapshot()
        resolved_host_api = selected_host_api if selected_host_api is not None else host_api
        current_host_api = str(resolved_host_api if resolved_host_api is not None else settings_snapshot.get("hostAPI", ""))
        host_api_options = self.audio_source_cache.get("host_api_options", [])

        if not current_host_api or current_host_api not in host_api_options:
            current_host_api = str(self.audio_source_cache.get("default_host_api", "")) or str(
                next((item for item in host_api_options if isinstance(item, str) and not item.startswith("[")), "")
            )

        if current_host_api:
            mic_options = self.audio_source_cache.get("mic_options_by_host", {}).get(current_host_api) or []
            speaker_options = self.audio_source_cache.get("speaker_options_by_host", {}).get(current_host_api) or []
        else:
            mic_options = self.audio_source_cache.get("mic_options_all", [])
            speaker_options = self.audio_source_cache.get("speaker_options_all", [])

        selected_mic = settings_snapshot.get("mic")
        selected_speaker = settings_snapshot.get("speaker")
        if selected_mic not in mic_options:
            default_mic = self.audio_source_cache.get("default_mic", "")
            selected_mic = default_mic if default_mic in mic_options else (mic_options[0] if mic_options else "")
        if selected_speaker not in speaker_options:
            default_speaker = self.audio_source_cache.get("default_speaker", "")
            selected_speaker = default_speaker if default_speaker in speaker_options else (speaker_options[0] if speaker_options else "")

        return {
            "host_api_options": host_api_options,
            "mic_options": mic_options,
            "speaker_options": speaker_options,
            "selected_host_api": current_host_api,
            "selected_mic": selected_mic,
            "selected_speaker": selected_speaker,
        }

    def get_audio_source_options(self, host_api: Optional[str] = None) -> JsonDict:
        return self.build_audio_source_options(host_api)

    def _build_compact_settings(self, settings_snapshot: Dict[str, object]) -> JsonDict:
        return {
            "log_level": settings_snapshot.get("log_level"),
            "dir_export": settings_snapshot.get("dir_export"),
            "dir_model": settings_snapshot.get("dir_model"),
            "export_to": settings_snapshot.get("export_to"),
            "source_lang_mw": settings_snapshot.get("source_lang_mw"),
            "target_lang_mw": settings_snapshot.get("target_lang_mw"),
            "input": settings_snapshot.get("input"),
            "tl_engine_mw": settings_snapshot.get("tl_engine_mw"),
            "transcribe_mw": settings_snapshot.get("transcribe_mw", True),
            "translate_mw": settings_snapshot.get("translate_mw", True),
            "auto_scroll_log": settings_snapshot.get("auto_scroll_log"),
            "auto_refresh_log": settings_snapshot.get("auto_refresh_log"),
            "source_lang_f_import": settings_snapshot.get("source_lang_f_import"),
            "target_lang_f_import": settings_snapshot.get("target_lang_f_import"),
            "transcribe_f_import": settings_snapshot.get("transcribe_f_import"),
            "translate_f_import": settings_snapshot.get("translate_f_import"),
            "tl_engine_f_import": settings_snapshot.get("tl_engine_f_import"),
            "model_f_import": settings_snapshot.get("model_f_import"),
            "selenium_compact_level": settings_snapshot.get("selenium_compact_level", 2),
            "selenium_z_order_mode": settings_snapshot.get("selenium_z_order_mode", "behind-main"),
            "selenium_auto_close_on_task_done": settings_snapshot.get("selenium_auto_close_on_task_done", True),
            "selenium_chrome_user_data_dir": settings_snapshot.get("selenium_chrome_user_data_dir", ""),
            "enable_initial_prompt": settings_snapshot.get("enable_initial_prompt", False),
            "initial_prompts_map": settings_snapshot.get("initial_prompts_map", {}),
            "condition_on_previous_text": settings_snapshot.get("condition_on_previous_text", True),
            "filter_rec": settings_snapshot.get("filter_rec", True),
            "filter_rec_case_sensitive": settings_snapshot.get("filter_rec_case_sensitive", False),
            "filter_rec_strip": settings_snapshot.get("filter_rec_strip", True),
            "filter_rec_ignore_punctuations": settings_snapshot.get("filter_rec_ignore_punctuations", "\"',.?!"),
            "filter_rec_exact_match": settings_snapshot.get("filter_rec_exact_match", False),
            "filter_rec_similarity": settings_snapshot.get("filter_rec_similarity", 0.75),
            "filter_file_import": settings_snapshot.get("filter_file_import", True),
            "filter_file_import_case_sensitive": settings_snapshot.get("filter_file_import_case_sensitive", False),
            "filter_file_import_strip": settings_snapshot.get("filter_file_import_strip", True),
            "filter_file_import_ignore_punctuations": settings_snapshot.get("filter_file_import_ignore_punctuations", "\"',.?!"),
            "filter_file_import_exact_match": settings_snapshot.get("filter_file_import_exact_match", False),
            "filter_file_import_similarity": settings_snapshot.get("filter_file_import_similarity", 0.75),
        }
