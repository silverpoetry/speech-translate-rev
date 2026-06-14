from threading import Thread
from typing import Optional

from speech_translate._logging import init_logging
from speech_translate._path import dir_debug
from speech_translate.app_startup_controller import AppStartupController
from speech_translate.controller_protocols import JsonDict, TrayLike, WebviewWindowLike
from speech_translate.detached_windows import DetachedWindowManager
from speech_translate.detached_window_controller import DetachedWindowController
from speech_translate.import_queue_manager import ImportQueueController
from speech_translate.main_window_controller import MainWindowController
from speech_translate.model_manager import ModelManagerController
from speech_translate.recording_controller import RecordingSessionController
from speech_translate.runtime_bootstrap import add_ffmpeg_to_path, get_whisper_load_api, install_no_console_popen
from speech_translate.state_view_builder import StateViewBuilder
from speech_translate.system_settings_controller import DEFAULT_PATH_CONFIG, SystemSettingsController
from speech_translate.web_bridge_facade import WebBridgeFacadeMixin
from speech_translate.linker import sj
from speech_translate.web_backend import HeadlessFileProcessDialog, WebTaskBridge, headless_mbox
from speech_translate.ui_protocol import TASK_SOURCE_GENERAL
from speech_translate.utils.translate.language import TL_ENGINE_SOURCE_DICT, TL_ENGINE_TARGET_DICT
from speech_translate.utils.translate.translator import shutdown_selenium_translator

install_no_console_popen()


class WebBridge(WebBridgeFacadeMixin, WebTaskBridge):
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
        self.model_manager_controller = ModelManagerController(self, sj, get_whisper_load_api)
        self.import_queue_controller = ImportQueueController(self, sj, HeadlessFileProcessDialog, headless_mbox, shutdown_selenium_translator)
        self.recording_controller = RecordingSessionController(self, get_whisper_load_api, shutdown_selenium_translator)
        self.state_view_builder = StateViewBuilder(self, sj)
        path_config = dict(DEFAULT_PATH_CONFIG)
        path_config["dir_debug"] = dir_debug
        self.system_settings_controller = SystemSettingsController(self, sj, path_config)
        
        # --- Detached Windows ---
        self.detached_window_manager = DetachedWindowManager(self, sj)
        self.detached_window_controller = DetachedWindowController(self, sj, self.detached_window_manager)
        self.state_view_builder.start_audio_source_scan()

    def bind_window(self, window: WebviewWindowLike) -> None:
        super().bind_window(window)
        self.main_window_controller.bind_window(window)

    def bind_tray(self, tray: TrayLike) -> None:
        super().bind_tray(tray)

    def get_state(self) -> JsonDict:
        result = self.state_view_builder.build_state()
        if not self.main_window_controller.first_state_logged:
            self.main_window_controller.first_state_logged = True
            self._log_startup_marker("first_get_state")
        return result

    def get_task_state(self) -> JsonDict:
        return self.snapshot_task_state()

    def get_live_state(self) -> JsonDict:
        return self.snapshot_live_state()

    def update_task_message(self, message: str, source: str = TASK_SOURCE_GENERAL) -> None:
        super().update_task_message(message, source=source)
        self.model_manager_controller.handle_task_message(message, source=source)

def main(with_log_init: bool = True) -> None:
    startup_controller = AppStartupController(WebBridge, add_ffmpeg_to_path)
    startup_controller.start(with_log_init=with_log_init, log_initializer=init_logging)
