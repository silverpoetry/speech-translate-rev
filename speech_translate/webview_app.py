import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from platform import processor, release, system, version
from signal import SIGINT, signal
from threading import Thread
from typing import Any, Dict, Optional, List
from time import strftime, time

from loguru import logger

from speech_translate._constants import APP_NAME
from speech_translate._logging import init_logging
from speech_translate._path import dir_debug
from speech_translate._version import __version__
from speech_translate.app_tray import AppTray
from speech_translate.detached_windows import DetachedWindowManager
from speech_translate.detached_window_controller import DetachedWindowController
from speech_translate.import_queue_manager import ImportQueueController
from speech_translate.main_window_controller import MainWindowController
from speech_translate.model_manager import ModelManagerController
from speech_translate.recording_controller import RecordingSessionController
from speech_translate.state_view_builder import StateViewBuilder
from speech_translate.system_settings_controller import DEFAULT_PATH_CONFIG, SystemSettingsController
from speech_translate.linker import bc, sj
from speech_translate.window_geometry import resolve_window_placement
from speech_translate.web_backend import HeadlessFileProcessDialog, WebTaskBridge, headless_mbox
from speech_translate.utils.translate.language import TL_ENGINE_SOURCE_DICT, TL_ENGINE_TARGET_DICT
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


class WebBridge(WebTaskBridge):
    """
    Bridge exposed to the pywebview frontend.
    Handles all communication between the Web UI and the Python backend.
    """

    TL_ENGINE_SOURCE_DICT_REF = TL_ENGINE_SOURCE_DICT
    TL_ENGINE_TARGET_DICT_REF = TL_ENGINE_TARGET_DICT

    def __init__(self):
        super().__init__()
        # --- Lifecycle ---
        self.main_window_controller = MainWindowController(self, sj)
        self.model_manager_controller = ModelManagerController(self, sj, _get_whisper_load_api)
        self.import_queue_controller = ImportQueueController(self, sj, HeadlessFileProcessDialog, headless_mbox, shutdown_selenium_translator)
        self.recording_controller = RecordingSessionController(self, _get_whisper_load_api, shutdown_selenium_translator)
        self.state_view_builder = StateViewBuilder(self, sj)
        path_config = dict(DEFAULT_PATH_CONFIG)
        path_config["dir_debug"] = dir_debug
        self.system_settings_controller = SystemSettingsController(self, sj, path_config)
        
        # --- Detached Windows ---
        self.detached_window_manager = DetachedWindowManager(self, sj)
        self.detached_window_controller = DetachedWindowController(self, sj, self.detached_window_manager)
        self.state_view_builder.start_audio_source_scan()

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
        return self.system_settings_controller.open_directory(name)

    def select_directory(self, name: str) -> Dict[str, Any]:
        return self.system_settings_controller.select_directory(name)

    def open_link(self, url: str) -> Dict[str, str]:
        return self.system_settings_controller.open_link(url)

    def open_hallucination_filter(self, target: str) -> Dict[str, str]:
        return self.system_settings_controller.open_hallucination_filter(target)

    def notify(self, title: str, message: str) -> Dict[str, str]:
        return self.system_settings_controller.notify(title, message)

    # =========================================================================
    # SECTION 2: SYSTEM & SETTINGS STATE GENERATION
    # =========================================================================

    def _resolve_export_dir(self) -> str:
        return self.system_settings_controller.resolve_export_dir()

    def _resolve_log_dir(self) -> str:
        return self.system_settings_controller.resolve_log_dir()

    def _resolve_selenium_chrome_user_data_dir(self) -> str:
        return self.system_settings_controller.resolve_selenium_chrome_user_data_dir()

    def get_setting(self, key: str) -> Any:
        return self.system_settings_controller.get_setting(key)

    def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        return self.system_settings_controller.set_setting(key, value)

    def set_import_setting(self, key: str, value: Any) -> Dict[str, Any]:
        return self.system_settings_controller.set_import_setting(key, value)

    def set_record_setting(self, key: str, value: Any) -> Dict[str, Any]:
        return self.system_settings_controller.set_record_setting(key, value)

    def get_log_file_name(self) -> str:
        return self.system_settings_controller.get_log_file_name()

    def get_log_content(self) -> str:
        return self.system_settings_controller.get_log_content()

    def refresh_log(self) -> Dict[str, str]:
        return self.system_settings_controller.refresh_log()

    def clear_log(self) -> Dict[str, str]:
        return self.system_settings_controller.clear_log()

    def get_state(self) -> Dict[str, Any]:
        result = self.state_view_builder.build_state()
        if not self.main_window_controller.first_state_logged:
            self.main_window_controller.first_state_logged = True
            self._log_startup_marker("first_get_state")
        return result

    def reload_state(self) -> Dict[str, Any]:
        return self.state_view_builder.reload_state()

    def get_task_state(self) -> Dict[str, Any]:
        return self.snapshot_task_state()

    def get_live_state(self) -> Dict[str, Any]:
        return self.snapshot_live_state()

    def _build_main_ui(self) -> Dict[str, Any]:
        return self.state_view_builder.build_main_ui()

    def _build_record_device_ui(self, device: str) -> Dict[str, Any]:
        return self.state_view_builder.build_record_device_ui(device)

    def _build_record_ui(self) -> Dict[str, Any]:
        return self.state_view_builder.build_record_ui()

    def _build_about(self) -> Dict[str, Any]:
        return self.state_view_builder.build_about()

    def _build_audio_source_options(self, selected_host_api: Optional[str] = None) -> Dict[str, Any]:
        return self.state_view_builder.build_audio_source_options(selected_host_api)

    def get_audio_source_options(self, host_api: Optional[str] = None) -> Dict[str, Any]:
        return self.state_view_builder.get_audio_source_options(host_api)

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
        return self.detached_window_controller.get_detached_config(mode)

    def set_detached_config(self, mode: str, key: str, value: Any) -> Dict[str, Any]:
        return self.detached_window_controller.set_detached_config(mode, key, value)

    def create_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        return self.detached_window_controller.create_detached_window(mode, x, y)

    def toggle_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        return self.detached_window_controller.toggle_detached_window(mode, x, y)

    def show_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        return self.detached_window_controller.show_detached_window(mode)

    def hide_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        return self.detached_window_controller.hide_detached_window(mode)

    def close_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        return self.detached_window_controller.close_detached_window(mode)

    def update_detached_content(self, mode: str, html_content: str) -> Dict[str, Any]:
        return self.detached_window_controller.update_detached_content(mode, html_content)

    def update_detached_config(self, mode: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.detached_window_controller.update_detached_config(mode, config)

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
