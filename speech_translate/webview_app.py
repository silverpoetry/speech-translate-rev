import os
import subprocess
import sys
from importlib import import_module
from dataclasses import asdict, dataclass
from pathlib import Path
from platform import processor, release, system, version
from signal import SIGINT, signal
from threading import Thread
from typing import Any, Dict, Optional, List, cast
from time import sleep, strftime, time

from loguru import logger

from speech_translate._constants import APP_NAME
from speech_translate._logging import init_logging
from speech_translate._path import dir_debug, dir_export, dir_log, dir_user
from speech_translate._version import __version__
from speech_translate.app_tray import AppTray
from speech_translate.detached_windows import (
    DetachedWindowManager,
    build_detached_config,
    detached_setting_key,
    get_detached_live_content,
    normalize_detached_mode,
)
from speech_translate.import_queue_manager import ImportQueueController
from speech_translate.main_window_controller import MainWindowController
from speech_translate.model_manager import ModelManagerController
from speech_translate.recording_controller import RecordingSessionController
from speech_translate.linker import bc, sj
from speech_translate.window_geometry import resolve_window_placement
from speech_translate.web_backend import HeadlessFileProcessDialog, WebTaskBridge, headless_mbox
from speech_translate.utils.audio.device import (
    get_default_host_api,
    get_default_input_device,
    get_default_output_device,
    get_host_apis,
    get_input_devices,
    get_output_devices,
)
from speech_translate.utils.helper import native_notify, open_folder, open_url
from speech_translate.utils.whisper.helper import model_keys, model_select_dict, model_values
from speech_translate.utils.types import SettingDict
from speech_translate.utils.translate.language import TL_ENGINE_SOURCE_DICT, TL_ENGINE_TARGET_DICT, WHISPER_LANG_LIST
from speech_translate.utils.translate.translator import shutdown_selenium_translator


_whisper_load_api = None


def _get_whisper_load_api():
    global _whisper_load_api
    if _whisper_load_api is None:
        from speech_translate.utils.whisper import load as whisper_load

        _whisper_load_api = whisper_load
    return _whisper_load_api


class NoConsolePopen(subprocess.Popen):
    """Disable console windows when spawning subprocesses on Windows."""

    def __init__(self, args, **kwargs):
        if system() == "Windows" and "startupinfo" not in kwargs:
            kwargs["startupinfo"] = subprocess.STARTUPINFO()
            kwargs["startupinfo"].dwFlags |= subprocess.STARTF_USESHOWWINDOW
        super().__init__(args, **kwargs)


subprocess.Popen = NoConsolePopen


def add_ffmpeg_to_path(weak: bool = False) -> bool:
    """Add ffmpeg executables to PATH."""
    if getattr(sys, "frozen", False):
        from static_ffmpeg import _add_paths, run

        run.sys.stdout = sys.stderr
        if weak:
            has_ffmpeg = _add_paths._has("ffmpeg") is not None
            has_ffprobe = _add_paths._has("ffprobe") is not None
            if has_ffmpeg and has_ffprobe:
                return False

        ffmpeg, _ = run.get_or_fetch_platform_executables_else_raise()
        os.environ["PATH"] = os.pathsep.join([os.path.dirname(ffmpeg), os.environ["PATH"]])
        return True

    from static_ffmpeg import _add_paths

    return _add_paths.add_paths()


@dataclass

class AppState:
    app_name: str
    version: str
    os_name: str
    os_release: str
    os_version: str
    cpu: str
    settings: Dict[str, Any]
    import_ui: Dict[str, Any]
    main_ui: Dict[str, Any]
    record_ui: Dict[str, Any]
    runtime_model: Dict[str, Any]
    live_ui: Dict[str, Any]
    about: Dict[str, Any]
    log_level: str
    current_log: str
    log_content: str


class WebBridge(WebTaskBridge):
    """
    Bridge exposed to the pywebview frontend.
    Handles all communication between the Web UI and the Python backend.
    """

    def __init__(self):
        super().__init__()
        # --- Lifecycle ---
        self.main_window_controller = MainWindowController(self, sj)
        self.model_manager_controller = ModelManagerController(self, sj, _get_whisper_load_api)
        self.import_queue_controller = ImportQueueController(self, sj, HeadlessFileProcessDialog, headless_mbox, shutdown_selenium_translator)
        self.recording_controller = RecordingSessionController(self, _get_whisper_load_api, shutdown_selenium_translator)
        
        # --- Detached Windows ---
        self.detached_window_manager = DetachedWindowManager(self, sj)
        
        # --- Audio Devices ---
        self._audio_source_cache: Dict[str, Any] = {
            "host_api_options": [], "mic_options_by_host": {}, "speaker_options_by_host": {},
            "mic_options_all": [], "speaker_options_all": [],
        }
        self._audio_source_cache_ready = False
        self._audio_source_cache_loading = True
        Thread(target=self._prime_audio_source_cache, daemon=True).start()

    @property
    def _model_status_cache(self) -> Dict[str, Dict[str, Any]]:
        return self.model_manager_controller.model_status_cache

    @_model_status_cache.setter
    def _model_status_cache(self, value: Dict[str, Dict[str, Any]]) -> None:
        self.model_manager_controller.model_status_cache = value

    @property
    def _model_download_running(self) -> bool:
        return self.model_manager_controller.model_download_running

    @_model_download_running.setter
    def _model_download_running(self, value: bool) -> None:
        self.model_manager_controller.model_download_running = value

    @property
    def _model_load_running(self) -> bool:
        return self.model_manager_controller.model_load_running

    @_model_load_running.setter
    def _model_load_running(self, value: bool) -> None:
        self.model_manager_controller.model_load_running = value

    @property
    def _runtime_model_key(self) -> str:
        return self.model_manager_controller.runtime_model_key

    @_runtime_model_key.setter
    def _runtime_model_key(self, value: str) -> None:
        self.model_manager_controller.runtime_model_key = value

    @property
    def _runtime_model_loaded(self) -> bool:
        return self.model_manager_controller.runtime_model_loaded

    @_runtime_model_loaded.setter
    def _runtime_model_loaded(self, value: bool) -> None:
        self.model_manager_controller.runtime_model_loaded = value

    @property
    def _runtime_model_message(self) -> str:
        return self.model_manager_controller.runtime_model_message

    @_runtime_model_message.setter
    def _runtime_model_message(self, value: str) -> None:
        self.model_manager_controller.runtime_model_message = value

    @property
    def _model_manager_engine(self) -> str:
        return self.model_manager_controller.model_manager_engine

    @_model_manager_engine.setter
    def _model_manager_engine(self, value: str) -> None:
        self.model_manager_controller.model_manager_engine = value

    @property
    def _model_manager_model(self) -> str:
        return self.model_manager_controller.model_manager_model

    @_model_manager_model.setter
    def _model_manager_model(self, value: str) -> None:
        self.model_manager_controller.model_manager_model = value

    @property
    def _file_import_queue(self) -> List[Any]:
        return self.import_queue_controller.file_import_queue

    @_file_import_queue.setter
    def _file_import_queue(self, value: List[Any]) -> None:
        self.import_queue_controller.file_import_queue = value

    @property
    def _processing_queue(self) -> List[Dict[str, Any]]:
        return self.import_queue_controller.processing_queue

    @_processing_queue.setter
    def _processing_queue(self, value: List[Dict[str, Any]]) -> None:
        self.import_queue_controller.processing_queue = value

    @property
    def _record_worker_thread(self) -> Optional[Thread]:
        return self.recording_controller.record_worker_thread

    @_record_worker_thread.setter
    def _record_worker_thread(self, value: Optional[Thread]) -> None:
        self.recording_controller.record_worker_thread = value

    @property
    def recording_state(self) -> Dict[str, Any]:
        return self.recording_controller.recording_state

    @recording_state.setter
    def recording_state(self, value: Dict[str, Any]) -> None:
        self.recording_controller.recording_state = value

    # =========================================================================
    # SECTION 1: LIFECYCLE & WINDOW MANAGEMENT
    # =========================================================================

    def set_startup_t0(self, started_at: float) -> None:
        self.main_window_controller.set_startup_t0(started_at)

    def _log_startup_marker(self, marker: str) -> None:
        self.main_window_controller.log_startup_marker(marker)

    def mark_startup(self, marker: str) -> Dict[str, Any]:
        return self.main_window_controller.mark_startup(marker)

    def bind_window(self, window):
        super().bind_window(window)
        self.main_window_controller.bind_window(window)

    def show_main_window(self) -> None:
        self.main_window_controller.show_main_window()

    def _save_main_window_geometry(self, force: bool = False) -> None:
        self.main_window_controller.save_main_window_geometry(force=force)

    def bind_tray(self, tray):
        super().bind_tray(tray)

    def quit_app(self) -> None:
        self.main_window_controller.quit_app()

    def open_directory(self, name: str) -> Dict[str, str]:
        mapping = {"export": self._resolve_export_dir(), "log": self._resolve_log_dir(), "debug": dir_debug, "model": self._resolve_model_dir()}
        if target := mapping.get(name): open_folder(target)
        return {"target": target or ""}

    def select_directory(self, name: str) -> Dict[str, Any]:
        target_map = {
            "export": ("dir_export", self._resolve_export_dir()),
            "model": ("dir_model", self._resolve_model_dir()),
            "selenium_chrome": ("selenium_chrome_user_data_dir", self._resolve_selenium_chrome_user_data_dir()),
        }
        setting_info = target_map.get(str(name or "").strip().lower())
        if not setting_info: return {"ok": False, "message": "Unsupported directory target", "path": ""}
        
        setting_key, default_dir = setting_info
        if not (window := self.get_window()): return {"ok": False, "message": "Window not ready", "path": ""}

        try:
            webview = import_module("webview")
            file_dialog = getattr(getattr(webview, "FileDialog", object), "FOLDER", webview.FOLDER_DIALOG)
            selected = window.create_file_dialog(file_dialog, directory=default_dir)
        except Exception as exc:
            logger.exception(exc)
            return {"ok": False, "message": str(exc), "path": ""}

        if not selected: return {"ok": False, "message": "No folder selected", "path": default_dir}
        selected_path = str(selected[0] if isinstance(selected, (list, tuple)) else selected).strip()
        if not selected_path: return {"ok": False, "message": "No folder selected", "path": default_dir}

        sj.save_key(setting_key, selected_path)
        if setting_key == "dir_model": self.model_manager_controller.clear_model_status_cache()
        return {"ok": True, "message": "Directory selected", "path": selected_path, "setting": setting_key}

    def open_link(self, url: str) -> Dict[str, str]:
        open_url(url)
        return {"url": url}

    def open_hallucination_filter(self, target: str) -> Dict[str, str]:
        try:
            from speech_translate._path import p_filter_rec, p_filter_file_import
            from speech_translate.utils.whisper.helper import create_hallucination_filter
            path = p_filter_rec if target == "rec" else p_filter_file_import
            if not os.path.exists(path):
                create_hallucination_filter('rec' if target == "rec" else 'file')
            
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except Exception as e:
            logger.exception(e)
            return {"ok": False, "message": str(e)}

    def notify(self, title: str, message: str) -> Dict[str, str]:
        logger.info(f"{title}: {message}")
        return {"title": title, "message": message}

    # =========================================================================
    # SECTION 2: SYSTEM & SETTINGS STATE GENERATION
    # =========================================================================

    def _resolve_export_dir(self) -> str:
        configured = sj.cache.get("dir_export", "auto")
        return configured if configured != "auto" else dir_export

    def _resolve_log_dir(self) -> str:
        configured = sj.cache.get("dir_log", "auto")
        return configured if configured != "auto" else dir_log

    def _resolve_selenium_chrome_user_data_dir(self) -> str:
        configured = str(sj.cache.get("selenium_chrome_user_data_dir", "") or "").strip()
        return configured if configured else str(Path(dir_user) / "selenium_chrome_profile")

    def get_setting(self, key: str) -> Any:
        return sj.cache.get(key)

    def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key == "selenium_settings":
            payload = value if isinstance(value, dict) else {}
            compact = max(0, min(3, int(payload.get("compact_level", 2))))
            z_order_raw = str(payload.get("z_order_mode", "behind-main")).strip().lower()
            z_order = z_order_raw if z_order_raw in {"normal", "behind-main", "bottom"} else "behind-main"
            auto_close = bool(payload.get("auto_close_on_task_done", True))
            chrome_user_data_dir = str(payload.get("chrome_user_data_dir", "")).strip()

            sj.save_key("selenium_compact_level", compact)
            sj.save_key("selenium_z_order_mode", z_order)
            sj.save_key("selenium_auto_close_on_task_done", auto_close)
            sj.save_key("selenium_chrome_user_data_dir", chrome_user_data_dir)

            return {"key": key, "value": {
                "selenium_compact_level": sj.cache.get("selenium_compact_level", compact),
                "selenium_z_order_mode": sj.cache.get("selenium_z_order_mode", z_order),
                "selenium_auto_close_on_task_done": sj.cache.get("selenium_auto_close_on_task_done", auto_close),
                "selenium_chrome_user_data_dir": sj.cache.get("selenium_chrome_user_data_dir", chrome_user_data_dir),
            }}

        if key == "selenium_compact_level":
            value = max(0, min(3, int(value)))
        elif key == "selenium_z_order_mode":
            as_text = str(value).strip().lower()
            value = as_text if as_text in {"normal", "behind-main", "bottom"} else "behind-main"
        elif key == "selenium_auto_close_on_task_done":
            value = bool(value)
        elif key == "selenium_chrome_user_data_dir":
            value = str(value or "").strip()

        sj.save_key(key, value)
        if key == "log_level":
            from speech_translate._logging import change_log_level
            change_log_level(str(value))
        return {"key": key, "value": sj.cache.get(key)}

    def set_import_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key in {"model_f_import", "model_mw"}:
            value = value if value in model_select_dict else value
        sj.save_key(key, value)
        return {"key": key, "value": sj.cache.get(key)}

    def set_record_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key == "model_device_preference":
            normalized = str(value or "auto").strip().lower()
            value = normalized if value in {"auto", "cpu", "cuda"} else "auto"
        sj.save_key(key, value)
        return {"key": key, "value": sj.cache.get(key)}

    def get_log_file_name(self) -> str:
        from speech_translate._logging import current_log
        return current_log

    def get_log_content(self) -> str:
        from speech_translate._logging import current_log
        log_path = Path(dir_log) / current_log
        try: content = log_path.read_text(encoding="utf-8")
        except FileNotFoundError: return f"Log file not found: {log_path}"
        except Exception as exc:
            logger.exception(exc)
            return f"Failed to read log file: {exc}"

        return content[-200000:] if len(content) > 200000 else content

    def refresh_log(self) -> Dict[str, str]:
        return {"content": self.get_log_content(), "file": self.get_log_file_name()}

    def clear_log(self) -> Dict[str, str]:
        from speech_translate._logging import clear_current_log_file
        clear_current_log_file()
        logger.info("Log cleared from web UI")
        return self.refresh_log()

    def get_state(self) -> Dict[str, Any]:
        state_t0 = time()
        settings = dict(sj.cache)
        t_settings = time()
        
        compact_settings = {
            "theme": settings.get("theme"), "log_level": settings.get("log_level"), "dir_export": settings.get("dir_export"),
            "dir_model": settings.get("dir_model"), "export_to": settings.get("export_to"), "source_lang_mw": settings.get("source_lang_mw"),
            "target_lang_mw": settings.get("target_lang_mw"), "input": settings.get("input"), "tl_engine_mw": settings.get("tl_engine_mw"),
            "transcribe_mw": settings.get("transcribe_mw", True), "translate_mw": settings.get("translate_mw", True),
            "auto_scroll_log": settings.get("auto_scroll_log"), "auto_refresh_log": settings.get("auto_refresh_log"),
            "source_lang_f_import": settings.get("source_lang_f_import"), "target_lang_f_import": settings.get("target_lang_f_import"),
            "transcribe_f_import": settings.get("transcribe_f_import"), "translate_f_import": settings.get("translate_f_import"),
            "tl_engine_f_import": settings.get("tl_engine_f_import"), "model_f_import": settings.get("model_f_import"),
            "selenium_compact_level": settings.get("selenium_compact_level", 2), "selenium_z_order_mode": settings.get("selenium_z_order_mode", "behind-main"),
            "selenium_auto_close_on_task_done": settings.get("selenium_auto_close_on_task_done", True), "selenium_chrome_user_data_dir": settings.get("selenium_chrome_user_data_dir", ""),
            "enable_initial_prompt": settings.get("enable_initial_prompt", False), "initial_prompts_map": settings.get("initial_prompts_map", {}),
            "condition_on_previous_text": settings.get("condition_on_previous_text", True),
            "filter_rec": settings.get("filter_rec", True), "filter_rec_case_sensitive": settings.get("filter_rec_case_sensitive", False),
            "filter_rec_strip": settings.get("filter_rec_strip", True), "filter_rec_ignore_punctuations": settings.get("filter_rec_ignore_punctuations", "\"',.?!"),
            "filter_rec_exact_match": settings.get("filter_rec_exact_match", False), "filter_rec_similarity": settings.get("filter_rec_similarity", 0.75),
            "filter_file_import": settings.get("filter_file_import", True), "filter_file_import_case_sensitive": settings.get("filter_file_import_case_sensitive", False),
            "filter_file_import_strip": settings.get("filter_file_import_strip", True), "filter_file_import_ignore_punctuations": settings.get("filter_file_import_ignore_punctuations", "\"',.?!"),
            "filter_file_import_exact_match": settings.get("filter_file_import_exact_match", False), "filter_file_import_similarity": settings.get("filter_file_import_similarity", 0.75),
        }

        import_ui, t_import = self._build_import_ui(verify_available=False), time()
        main_ui, t_main = self._build_main_ui(), time()
        record_ui, t_record = self._build_record_ui(), time()
        runtime_model, t_runtime = self._build_runtime_model_state(), time()
        live_ui, t_live = self.snapshot_live_state(), time()
        about, t_about = self._build_about(), time()
        current_log, log_content, t_log = self.get_log_file_name(), self.get_log_content(), time()

        result = asdict(AppState(
            app_name=APP_NAME, version=__version__, os_name=system(), os_release=release(),
            os_version=version(), cpu=processor(), settings=compact_settings, import_ui=import_ui,
            main_ui=main_ui, record_ui=record_ui, runtime_model=runtime_model, live_ui=live_ui,
            about=about, log_level=sj.cache.get("log_level", "DEBUG"), current_log=current_log, log_content=log_content,
        ))
        result["detached_config"] = {"tc": self.get_detached_config("tc"), "tl": self.get_detached_config("tl")}

        if not self.main_window_controller.first_state_logged:
            self.main_window_controller.first_state_logged = True
            self._log_startup_marker("first_get_state")
        return result

    def reload_state(self) -> Dict[str, Any]:
        return self.get_state()

    def get_task_state(self) -> Dict[str, Any]:
        return self.snapshot_task_state()

    def get_live_state(self) -> Dict[str, Any]:
        return self.snapshot_live_state()

    def _build_main_ui(self) -> Dict[str, Any]:
        s = dict(sj.cache)
        return {
            "input_options": ["mic", "speaker"], "source_options": WHISPER_LANG_LIST, "target_options": WHISPER_LANG_LIST,
            "engine_options": ["Selenium Chrome Translate", "Google Translate", "MyMemoryTranslator", "LibreTranslate"],
            "selected_input": s.get("input"), "selected_source": s.get("source_lang_mw"), "selected_target": s.get("target_lang_mw"),
            "selected_engine": s.get("tl_engine_mw"), "transcribe": s.get("transcribe_mw", True), "translate": s.get("translate_mw", True),
            "auto_scroll_log": s.get("auto_scroll_log"), "auto_refresh_log": s.get("auto_refresh_log"),
        }

    def _build_record_device_ui(self, device: str) -> Dict[str, Any]:
        s = dict(sj.cache)
        return {
            "sample_rate": s.get(f"sample_rate_{device}"), "chunk_size": s.get(f"chunk_size_{device}"), "channels": s.get(f"channels_{device}"),
            "auto_sample_rate": s.get(f"auto_sample_rate_{device}"), "auto_channels": s.get(f"auto_channels_{device}"),
            "min_input": s.get(f"min_input_length_{device}"), "max_buffer": s.get(f"max_buffer_{device}"), "max_sentences": s.get(f"max_sentences_{device}"),
            "no_limit": s.get(f"{device}_no_limit"), "threshold_enable": s.get(f"threshold_enable_{device}"), "threshold_auto": s.get(f"threshold_auto_{device}"),
            "auto_break_buffer": s.get(f"auto_break_buffer_{device}"), "threshold_auto_level": s.get(f"threshold_auto_level_{device}"),
            "threshold_auto_silero": s.get(f"threshold_auto_silero_{device}"), "threshold_silero_min": s.get(f"threshold_silero_{device}_min"),
            "threshold_db": s.get(f"threshold_db_{device}"),
        }

    def _build_record_ui(self) -> Dict[str, Any]:
        s = dict(sj.cache)
        audio_sources = self._build_audio_source_options()
        return {
            "input": s.get("input"), "host_api": s.get("hostAPI"), "mic": s.get("mic"), "speaker": s.get("speaker"),
            "host_api_options": audio_sources.get("host_api_options", []), "mic_options": audio_sources.get("mic_options", []),
            "speaker_options": audio_sources.get("speaker_options", []), "verbose_record": s.get("verbose_record"),
            "transcribe_rate": s.get("transcribe_rate"), "model_device_preference": s.get("model_device_preference", "auto"),
            "model_device_options": ["auto", "cpu", "cuda"], "separate_with": s.get("separate_with"),
            "use_temp": s.get("use_temp"), "keep_temp": s.get("keep_temp"), "file_use_official_whisper": s.get("file_use_official_whisper", False),
            "show_audio_visualizer_in_setting": s.get("show_audio_visualizer_in_setting"),
            "mic_device": self._build_record_device_ui("mic"), "speaker_device": self._build_record_device_ui("speaker"),
        }

    def _build_about(self) -> Dict[str, Any]:
        return {
            "name": APP_NAME, "version": __version__, "os": f"{system()} {release()} {version()}", "cpu": processor(),
            "log_file": self.get_log_file_name(), "model_dir": self._resolve_model_dir(), "export_dir": self._resolve_export_dir(),
        }

    # =========================================================================
    # SECTION 3: AUDIO DEVICE SCANNING
    # =========================================================================

    def _prime_audio_source_cache(self) -> None:
        try:
            host_api_options = get_host_apis()
            mic_options_all = get_input_devices("")
            speaker_options_all = get_output_devices("")

            ok_host, host_info = get_default_host_api()
            default_host_api = str(host_info.get("name", "")) if ok_host and isinstance(host_info, dict) else ""

            def find_default(device_info, all_options):
                if not device_info or not isinstance(device_info, dict): return ""
                name = str(device_info.get("name", ""))
                return next((str(item) for item in all_options if isinstance(item, str) and "[ID:" in item and name.lower() in item.lower()), "") if name else ""

            default_mic = find_default(get_default_input_device()[1], mic_options_all)
            default_speaker = find_default(get_default_output_device()[1], speaker_options_all)

            mic_options_by_host, speaker_options_by_host = {}, {}
            for host_api in host_api_options:
                if isinstance(host_api, str) and not host_api.startswith("["):
                    mic_options_by_host[host_api] = get_input_devices(str(host_api))
                    speaker_options_by_host[host_api] = get_output_devices(str(host_api))

            self._audio_source_cache = {
                "host_api_options": host_api_options, "mic_options_by_host": mic_options_by_host,
                "speaker_options_by_host": speaker_options_by_host, "mic_options_all": mic_options_all,
                "speaker_options_all": speaker_options_all, "default_host_api": default_host_api,
                "default_mic": default_mic, "default_speaker": default_speaker,
            }
        except Exception as exc:
            logger.exception(exc)
            self._audio_source_cache = {
                "host_api_options": [], "mic_options_by_host": {}, "speaker_options_by_host": {},
                "mic_options_all": ["[ERROR] Failed to load input devices"], "speaker_options_all": ["[ERROR] Failed to load output devices"],
                "default_host_api": "", "default_mic": "", "default_speaker": "",
            }
        finally:
            self._audio_source_cache_loading = False
            self._audio_source_cache_ready = True
            try: self._emit_ui_update(["state"])
            except Exception: pass

    def _build_audio_source_options(self, selected_host_api: Optional[str] = None) -> Dict[str, Any]:
        s = dict(sj.cache)
        host_api = str(selected_host_api if selected_host_api is not None else s.get("hostAPI", ""))
        host_api_options = self._audio_source_cache.get("host_api_options", [])
        
        if not host_api or host_api not in host_api_options:
            host_api = str(self._audio_source_cache.get("default_host_api", "")) or str(next((x for x in host_api_options if isinstance(x, str) and not x.startswith("[")), ""))

        if host_api:
            mic_options = self._audio_source_cache.get("mic_options_by_host", {}).get(host_api) or []
            speaker_options = self._audio_source_cache.get("speaker_options_by_host", {}).get(host_api) or []
        else:
            mic_options = self._audio_source_cache.get("mic_options_all", [])
            speaker_options = self._audio_source_cache.get("speaker_options_all", [])

        selected_mic, selected_speaker = s.get("mic"), s.get("speaker")
        if selected_mic not in mic_options:
            selected_mic = self._audio_source_cache.get("default_mic", "") if self._audio_source_cache.get("default_mic", "") in mic_options else (mic_options[0] if mic_options else "")
        if selected_speaker not in speaker_options:
            selected_speaker = self._audio_source_cache.get("default_speaker", "") if self._audio_source_cache.get("default_speaker", "") in speaker_options else (speaker_options[0] if speaker_options else "")

        return {
            "host_api_options": host_api_options, "mic_options": mic_options, "speaker_options": speaker_options,
            "selected_host_api": host_api, "selected_mic": selected_mic, "selected_speaker": selected_speaker,
        }

    def get_audio_source_options(self, host_api: Optional[str] = None) -> Dict[str, Any]:
        return self._build_audio_source_options(host_api)

    # =========================================================================
    # SECTION 4: MODEL MANAGEMENT
    # =========================================================================

    def _resolve_model_dir(self) -> str:
        return self.model_manager_controller.resolve_model_dir()

    def _get_model_manager_keys(self) -> list[str]:
        return self.model_manager_controller.get_model_manager_keys()

    def _normalize_model_key(self, value: str) -> str:
        return self.model_manager_controller.normalize_model_key(value)

    def _normalize_engine_name(self, value: str) -> str:
        return self.model_manager_controller.normalize_engine_name(value)

    def _is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        return self.model_manager_controller.is_model_available_for_backend(model_key, backend, model_dir)

    def _verify_model_status(self, engine: str, model_key: str, model_dir: str) -> tuple[bool, str]:
        return self.model_manager_controller.verify_model_status(engine, model_key, model_dir)

    def _cache_model_status(self, engine: str, model_key: str, downloaded: bool, error: str = "", downloading: bool = False, progress: Optional[float] = None, speed: str = "") -> None:
        self.model_manager_controller.cache_model_status(engine, model_key, downloaded, error, downloading, progress, speed)

    @staticmethod
    def _path_size(path: str) -> int:
        return ModelManagerController.path_size(path)

    @staticmethod
    def _fmt_bytes(value: float) -> str:
        return ModelManagerController.format_bytes(value)

    def _estimate_total_whisper_bytes(self, model_key: str) -> int:
        return self.model_manager_controller.estimate_total_whisper_bytes(model_key)

    def _build_model_manager_state(self, engine_hint: Optional[str] = None, include_both: bool = False) -> Dict[str, Any]:
        return self.model_manager_controller.build_model_manager_state(engine_hint, include_both)

    def _build_runtime_model_state(self) -> Dict[str, Any]:
        return self.model_manager_controller.build_runtime_model_state()

    def get_model_manager_state(self, engine: Optional[str] = None) -> Dict[str, Any]:
        return self.model_manager_controller.get_model_manager_state(engine)

    def get_runtime_model_state(self) -> Dict[str, Any]:
        return self.model_manager_controller.get_runtime_model_state()

    def check_model(self, model_key: str, engine: str = "whisper") -> Dict[str, Any]:
        return self.model_manager_controller.check_model(model_key, engine)

    def check_all_models(self, engine: str = "whisper") -> Dict[str, Any]:
        return self.model_manager_controller.check_all_models(engine)

    def download_model(self, model_key: str, engine: str = "whisper") -> Dict[str, Any]:
        return self.model_manager_controller.download_model(model_key, engine)

    def load_runtime_model(self, model_key: str) -> Dict[str, Any]:
        return self.model_manager_controller.load_runtime_model(model_key)

    # =========================================================================
    # SECTION 5: REALTIME RECORDING
    # =========================================================================
    def _wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        return self.recording_controller.wait_recording_idle(timeout_s=timeout_s)

    def update_task_message(self, message: str, source: str = "general"):
        super().update_task_message(message, source=source)
        self.model_manager_controller.handle_task_message(message)

    def set_recording_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.recording_controller.set_recording_state(payload)

    def get_recording_state(self) -> Dict[str, Any]:
        return self.recording_controller.get_recording_state()

    def start_recording(self, device: str = "mic", lang_source: str = "English", lang_target: str = "Indonesian", engine: str = "Selenium Chrome Translate", is_tc: bool = True, is_tl: bool = True) -> Dict[str, Any]:
        return self.recording_controller.start_recording(device, lang_source, lang_target, engine, is_tc, is_tl)

    def stop_recording(self) -> Dict[str, Any]:
        return self.recording_controller.stop_recording()

    # =========================================================================
    # SECTION 6: BATCH FILE PROCESSING QUEUE & UI SYNC
    # =========================================================================
    def get_import_ui_details(self) -> Dict[str, Any]:
        return self.import_queue_controller.get_import_ui_details()

    def _build_import_ui(self, verify_available: bool = True) -> Dict[str, Any]:
        return self.import_queue_controller.build_import_ui(verify_available=verify_available)

    def _get_full_display_queue(self) -> List[Dict[str, Any]]:
        return self.import_queue_controller.get_full_display_queue()

    def get_file_processing_state(self) -> Dict[str, Any]:
        return self.import_queue_controller.get_file_processing_state()

    def init_file_batch(self, task_name: str, files: list):
        self.import_queue_controller.init_file_batch(task_name, files)

    def sync_file_status(self, index: int, combined_status: str, is_completed: bool):
        self.import_queue_controller.sync_file_status(index, combined_status, is_completed)

    def add_files_to_import_queue(self, files: Optional[list[str]] = None) -> Dict[str, Any]:
        return self.import_queue_controller.add_files_to_import_queue(files)

    def remove_file_from_import_queue(self, index: Optional[int] = None) -> Dict[str, Any]:
        return self.import_queue_controller.remove_file_from_import_queue(index)

    def clear_import_queue(self) -> Dict[str, Any]:
        return self.import_queue_controller.clear_import_queue()

    def import_files(self, files: Optional[list[str]] = None) -> Dict[str, Any]:
        return self.import_queue_controller.import_files(files)

    def start_import_queue(self) -> Dict[str, Any]:
        return self.import_queue_controller.start_import_queue()

    def stop_import_queue(self) -> Dict[str, Any]:
        return self.import_queue_controller.stop_import_queue()

    # =========================================================================
    # SECTION 7: DETACHED WINDOWS
    # =========================================================================

    def get_detached_config(self, mode: str) -> Dict[str, Any]:
        return build_detached_config(sj.cache, mode)

    def set_detached_config(self, mode: str, key: str, value: Any) -> Dict[str, Any]:
        normalized_mode = normalize_detached_mode(mode)
        setting_key = detached_setting_key(normalized_mode, key)
        sj.save_key(setting_key, value)
        return {"key": setting_key, "value": sj.cache.get(setting_key)}

    def create_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        mode = normalize_detached_mode(mode)
        placement = resolve_window_placement(
            sj.cache.get(f"ex_{mode}_geometry", "900x240"),
            900,
            240,
            x=x,
            y=y,
        )
        self.detached_window_manager.create_window(mode, placement.x, placement.y, placement.width, placement.height)
        self.update_detached_config(mode)

        if html := get_detached_live_content(mode, self.snapshot_live_state()):
            self.update_detached_content(mode, html)
        return {"status": "created", "mode": mode}

    def toggle_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        mode = normalize_detached_mode(mode)
        if mode in self.detached_window_manager.windows:
            self.detached_window_manager.close_window(mode)
            return {"status": "closed", "mode": mode}
        return self.create_detached_window(mode, x, y)

    def show_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        mode = normalize_detached_mode(mode)
        self.detached_window_manager.show_window(mode)
        return {"status": "shown", "mode": mode}

    def hide_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        mode = normalize_detached_mode(mode)
        self.detached_window_manager.hide_window(mode)
        return {"status": "hidden", "mode": mode}

    def close_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        mode = normalize_detached_mode(mode)
        self.detached_window_manager.close_window(mode)
        return {"status": "closed", "mode": mode}

    def update_detached_content(self, mode: str, html_content: str) -> Dict[str, Any]:
        mode = normalize_detached_mode(mode)
        if mode not in self.detached_window_manager.windows: return {"status": "missing", "mode": mode}
        self.detached_window_manager.update_window_content(mode, html_content)
        return {"status": "updated", "mode": mode}

    def update_detached_config(self, mode: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        mode = normalize_detached_mode(mode)
        self.detached_window_manager.update_window_config(mode, config or self.get_detached_config(mode))
        return {"status": "config_updated", "mode": mode}

def _install_signal_handler():
    def signal_handler(_sig, _frame):
        logger.info("Received Ctrl+C, exiting...")
        bridge = getattr(bc, "web_bridge", None)
        if bridge is not None:
            bridge.quit_app()

    signal(SIGINT, signal_handler)


def _build_html_path() -> str:
    return str(Path(__file__).with_name("web") / "index.html")


def main(with_log_init: bool = True):
    startup_t0 = time()
    if with_log_init:
        init_logging(sj.cache["log_level"])

    logger.info(f"App Version: {__version__} - TIME: {strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"OS: {system()} {release()} {version()} | CPU: {processor()}")
    logger.debug(f"Sys args: {sys.argv}")
    logger.debug("Loading Web UI...")

    _install_signal_handler()
    logger.debug("[Startup] before_add_ffmpeg")
    add_ffmpeg_to_path(weak=True)
    logger.debug("[Startup] after_add_ffmpeg")
    logger.debug("[Startup] before_import_webview")
    webview = import_module("webview")
    logger.debug("[Startup] after_import_webview")

    logger.debug("[Startup] before_bridge_init")
    bridge = WebBridge()
    logger.debug("[Startup] after_bridge_init")
    bridge.set_startup_t0(startup_t0)
    setattr(bc, "web_bridge", bridge)

    tray_enabled = "--no-tray" not in sys.argv

    raw_main_size = str(sj.cache.get("mw_size", "980x620") or "980x620").strip()
    if raw_main_size == "1140x680":
        # One-time migration from legacy default to the new smaller default.
        sj.save_key("mw_size", "980x620")
        raw_main_size = "980x620"

    main_placement = resolve_window_placement(raw_main_size, 980, 620)

    bridge._log_startup_marker("before_create_main_window")
    window = webview.create_window(
        APP_NAME,
        _build_html_path(),
        js_api=bridge,
        width=main_placement.width,
        height=main_placement.height,
        x=main_placement.x,
        y=main_placement.y,
        min_size=(880, 560),
        hidden=True,
    )
    bridge._log_startup_marker("after_create_main_window")
    bridge.bind_window(window)

    debug_enabled = "--debug-webview" in sys.argv or "--debug" in sys.argv
    bridge._log_startup_marker("before_webview_start")

    def _on_webview_ready():
        bridge._log_startup_marker("webview_ready_callback")
        if tray_enabled and bridge.get_tray() is None:
            try:
                bridge._log_startup_marker("before_tray_init")
                tray = AppTray(bridge)
                bridge.bind_tray(tray)
                bridge._log_startup_marker("after_tray_init")
            except Exception as exc:
                logger.exception(exc)

    webview.start(_on_webview_ready, debug=debug_enabled)
