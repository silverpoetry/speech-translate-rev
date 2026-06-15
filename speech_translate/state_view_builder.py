from __future__ import annotations

from dataclasses import asdict, dataclass
from platform import processor, release, system, version
from threading import Thread
from typing import Dict, Optional

from speech_translate._constants import APP_NAME
from speech_translate._version import __version__
from speech_translate.controller_protocols import JsonDict, SettingsStore, StateViewBridge
from speech_translate.log_helpers import logger
from speech_translate.state_view_settings import (
    build_record_device_view_settings,
    build_state_view_settings,
)
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
        view_settings = build_state_view_settings(self._settings_snapshot())
        return AppState(
            app_name=APP_NAME,
            version=__version__,
            os_name=system(),
            os_release=release(),
            os_version=version(),
            cpu=processor(),
            settings=view_settings.compact_settings.to_payload(),
            import_ui=self.bridge.build_import_ui(verify_available=False),
            main_ui=self.build_main_ui(),
            record_ui=self.build_record_ui(),
            runtime_model=self.bridge.build_runtime_model_state(),
            live_ui=self.bridge.snapshot_live_state(),
            about=self.build_about(),
            log_level=view_settings.log_level,
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
        view_settings = build_state_view_settings(self._settings_snapshot())
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
            **view_settings.main_ui.to_payload(),
        }

    def build_record_device_ui(self, device: str) -> JsonDict:
        return build_record_device_view_settings(self._settings_snapshot(), device).to_payload()

    def build_record_ui(self) -> JsonDict:
        view_settings = build_state_view_settings(self._settings_snapshot())
        audio_sources = self.build_audio_source_options()
        return view_settings.record_ui.to_payload(audio_sources=audio_sources)

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
