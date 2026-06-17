from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from speech_translate._logging import init_logging
from speech_translate._path import dir_debug
from speech_translate.app_startup_controller import AppStartupController
from speech_translate.app_runtime import get_runtime_root
from speech_translate.controller_protocols import JsonDict, SettingsStore, TrayLike, WebviewWindowLike
from speech_translate.detached_window_controller import DetachedWindowController
from speech_translate.detached_windows import DetachedWindowManager
from speech_translate.import_queue_manager import ImportQueueController
from speech_translate.import_queue_runtime import ImportQueueRuntimeBindings
from speech_translate.main_window_controller import MainWindowController
from speech_translate.model_manager import ModelManagerController
from speech_translate.recording_controller import RecordingSessionController
from speech_translate.runtime_registry import set_current_bridge, settings_registry
from speech_translate.runtime_bootstrap import add_ffmpeg_to_path, get_whisper_load_api, install_no_console_popen
from speech_translate.state_view_builder import StateViewBuilder, StateViewDependencies
from speech_translate.system_settings_controller import DEFAULT_PATH_CONFIG, SystemSettingsController
from speech_translate.ui_protocol import TASK_SOURCE_GENERAL
from speech_translate.utils.translate.language import TL_ENGINE_SOURCE_DICT, TL_ENGINE_TARGET_DICT
from speech_translate.utils.translate.translator import shutdown_selenium_translator
from speech_translate.web_backend import WebTaskBridge
from speech_translate.web_bridge_api import WebBridgeApiMixin


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


def _get_default_settings() -> SettingsStore:
    return settings_registry.get()


def _resolve_configured_log_dir(settings: SettingsStore) -> str | None:
    configured = str(settings.cache.get("dir_log", "auto") or "auto").strip()
    return None if configured == "auto" else configured


def build_web_bridge_dependencies(bridge: "WebBridge", settings: SettingsStore | None = None) -> WebBridgeDependencies:
    settings = settings or _get_default_settings()
    runtime_root = get_runtime_root()
    main_window_controller = MainWindowController(bridge, settings)
    model_manager_controller = ModelManagerController(bridge, settings, get_whisper_load_api)
    recording_controller = RecordingSessionController(
        bridge,
        get_whisper_load_api,
        shutdown_selenium_translator,
        model_manager_controller,
    )
    system_settings_controller = SystemSettingsController(
        bridge,
        settings,
        _default_path_config(),
        model_manager_controller,
    )
    detached_window_manager = DetachedWindowManager(bridge, settings)
    detached_window_controller = DetachedWindowController(bridge, settings, detached_window_manager)
    import_queue_controller = ImportQueueController(
        bridge,
        settings,
        shutdown_selenium_translator,
        recording_controller,
        model_manager_controller,
        runtime_bindings=ImportQueueRuntimeBindings(
            recording_state=runtime_root.recording_runtime,
            file_state=runtime_root.file_runtime,
            visual_state=runtime_root.visual,
        ),
    )
    state_view_builder = StateViewBuilder(
        StateViewDependencies(
            import_queue_controller=import_queue_controller,
            model_manager_controller=model_manager_controller,
            system_settings_controller=system_settings_controller,
            detached_window_controller=detached_window_controller,
            snapshot_live_state=bridge.snapshot_live_state,
            emit_ui_update=bridge.emit_ui_update,
        ),
        settings,
    )
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


class WebBridge(WebBridgeApiMixin, WebTaskBridge):
    """Bridge exposed to the pywebview frontend."""

    TL_ENGINE_SOURCE_DICT_REF = TL_ENGINE_SOURCE_DICT
    TL_ENGINE_TARGET_DICT_REF = TL_ENGINE_TARGET_DICT

    def __init__(
        self,
        *,
        dependencies_builder: Callable[["WebBridge"], WebBridgeDependencies] = build_web_bridge_dependencies,
        bootstrapper: BridgeBootstrapper | None = configure_runtime_bootstrap,
        settings: SettingsStore | None = None,
    ) -> None:
        self.settings = settings or _get_default_settings()
        super().__init__(settings=self.settings)
        set_current_bridge(self)
        if bootstrapper is not None:
            bootstrapper()
        dependencies = dependencies_builder(self, self.settings)
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

    def set_startup_t0(self, started_at: float) -> None:
        self.main_window_controller.set_startup_t0(started_at)

    def log_startup_marker(self, marker: str) -> None:
        self.main_window_controller.log_startup_marker(marker)

    def quit_app(self) -> None:
        try:
            self.import_queue_controller.stop_import_queue()
        except Exception:
            pass
        try:
            self.recording_controller.stop_recording()
        except Exception:
            pass
        try:
            shutdown_selenium_translator()
        except Exception:
            pass
        self.main_window_controller.quit_app()

    def get_state(self) -> JsonDict:
        result = self.state_view_builder.build_state()
        if not self.main_window_controller.first_state_logged:
            self.main_window_controller.first_state_logged = True
            self.log_startup_marker("first_get_state")
        return result

    def get_task_state(self) -> JsonDict:
        return self.snapshot_task_state()

    def get_live_state(self) -> JsonDict:
        return self.snapshot_live_state()

    def update_task_message(self, message: str, source: str = TASK_SOURCE_GENERAL) -> None:
        super().update_task_message(message, source=source)
        self.model_manager_controller.handle_task_message(message, source=source)

    def _sync_detached_live_target(self, target: str) -> None:
        detached_controller = getattr(self, "detached_window_controller", None)
        if detached_controller is None:
            return

        mode: str | None = None
        if target.startswith("detached_transcribed"):
            mode = "tc"
        elif target.startswith("detached_translated"):
            mode = "tl"
        if mode is None:
            return

        html_key = "detached_transcribed_html" if mode == "tc" else "detached_translated_html"
        html = str(self.snapshot_live_state().get(html_key, "") or "")
        detached_controller.update_detached_content(mode, html)

    def update_live_html(self, target: str, html: str) -> None:
        super().update_live_html(target, html)
        self._sync_detached_live_target(target)

    def append_live_text(self, target: str, text: str, separator: str = "") -> None:
        super().append_live_text(target, text, separator)
        self._sync_detached_live_target(target)

    def clear_live(self, prefix: str = "") -> None:
        super().clear_live(prefix)
        if not prefix:
            self._sync_detached_live_target("detached_transcribed_html")
            self._sync_detached_live_target("detached_translated_html")
            return
        if prefix.startswith("detached_transcribed"):
            self._sync_detached_live_target("detached_transcribed_html")
        if prefix.startswith("detached_translated"):
            self._sync_detached_live_target("detached_translated_html")


def main(with_log_init: bool = True) -> None:
    settings = _get_default_settings()
    startup_controller = AppStartupController(
        lambda: WebBridge(settings=settings),
        add_ffmpeg_to_path,
        settings=settings,
    )
    startup_controller.start(
        with_log_init=with_log_init,
        log_initializer=lambda level: init_logging(level, _resolve_configured_log_dir(settings)),
    )
