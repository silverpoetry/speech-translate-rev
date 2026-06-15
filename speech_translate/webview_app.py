from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from speech_translate._logging import init_logging
from speech_translate._path import dir_debug
from speech_translate.app_startup_controller import AppStartupController
from speech_translate.controller_protocols import JsonDict, TrayLike, WebviewWindowLike
from speech_translate.detached_window_controller import DetachedWindowController
from speech_translate.detached_windows import DetachedWindowManager
from speech_translate.import_queue_manager import ImportQueueController
from speech_translate.linker import sj
from speech_translate.main_window_controller import MainWindowController
from speech_translate.model_manager import ModelManagerController
from speech_translate.recording_controller import RecordingSessionController
from speech_translate.runtime_bootstrap import add_ffmpeg_to_path, get_whisper_load_api, install_no_console_popen
from speech_translate.state_view_builder import StateViewBuilder
from speech_translate.system_settings_controller import DEFAULT_PATH_CONFIG, SystemSettingsController
from speech_translate.ui_protocol import TASK_SOURCE_GENERAL
from speech_translate.utils.translate.language import TL_ENGINE_SOURCE_DICT, TL_ENGINE_TARGET_DICT
from speech_translate.utils.translate.translator import shutdown_selenium_translator
from speech_translate.web_backend import WebTaskBridge
from speech_translate.web_bridge_facade import WebBridgeFacadeMixin


BridgeBootstrapper = Callable[[], None]


@dataclass(frozen=True)
class WebBridgeDependencies:
    main_window_controller: MainWindowController
    model_manager_controller: ModelManagerController
    import_queue_controller: ImportQueueController
    recording_controller: RecordingSessionController
    state_view_builder: StateViewBuilder
    system_settings_controller: SystemSettingsController
    detached_window_manager: DetachedWindowManager
    detached_window_controller: DetachedWindowController


def _default_path_config() -> dict[str, str]:
    path_config = dict(DEFAULT_PATH_CONFIG)
    path_config["dir_debug"] = dir_debug
    return path_config


def configure_runtime_bootstrap() -> None:
    install_no_console_popen()


def build_web_bridge_dependencies(bridge: "WebBridge") -> WebBridgeDependencies:
    main_window_controller = MainWindowController(bridge, sj)
    model_manager_controller = ModelManagerController(bridge, sj, get_whisper_load_api)
    import_queue_controller = ImportQueueController(bridge, sj, shutdown_selenium_translator)
    recording_controller = RecordingSessionController(bridge, get_whisper_load_api, shutdown_selenium_translator)
    state_view_builder = StateViewBuilder(bridge, sj)
    system_settings_controller = SystemSettingsController(bridge, sj, _default_path_config())
    detached_window_manager = DetachedWindowManager(bridge, sj)
    detached_window_controller = DetachedWindowController(bridge, sj, detached_window_manager)
    return WebBridgeDependencies(
        main_window_controller=main_window_controller,
        model_manager_controller=model_manager_controller,
        import_queue_controller=import_queue_controller,
        recording_controller=recording_controller,
        state_view_builder=state_view_builder,
        system_settings_controller=system_settings_controller,
        detached_window_manager=detached_window_manager,
        detached_window_controller=detached_window_controller,
    )


class WebBridge(WebBridgeFacadeMixin, WebTaskBridge):
    """Bridge exposed to the pywebview frontend."""

    TL_ENGINE_SOURCE_DICT_REF = TL_ENGINE_SOURCE_DICT
    TL_ENGINE_TARGET_DICT_REF = TL_ENGINE_TARGET_DICT

    def __init__(
        self,
        *,
        dependencies_builder: Callable[["WebBridge"], WebBridgeDependencies] = build_web_bridge_dependencies,
        bootstrapper: BridgeBootstrapper | None = configure_runtime_bootstrap,
    ) -> None:
        super().__init__()
        if bootstrapper is not None:
            bootstrapper()
        dependencies = dependencies_builder(self)
        self.main_window_controller = dependencies.main_window_controller
        self.model_manager_controller = dependencies.model_manager_controller
        self.import_queue_controller = dependencies.import_queue_controller
        self.recording_controller = dependencies.recording_controller
        self.state_view_builder = dependencies.state_view_builder
        self.system_settings_controller = dependencies.system_settings_controller
        self.detached_window_manager = dependencies.detached_window_manager
        self.detached_window_controller = dependencies.detached_window_controller
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
