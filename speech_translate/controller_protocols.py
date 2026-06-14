from __future__ import annotations

from typing import Callable, MutableMapping, Protocol, Sequence


JsonDict = dict[str, object]


class SettingsStore(Protocol):
    cache: MutableMapping[str, object]

    def save_key(self, key: str, value: object) -> None:
        ...


class FolderDialogWindow(Protocol):
    def create_file_dialog(self, file_dialog: object, directory: str | None = None) -> object:
        ...

    width: int
    height: int
    native: object | None
    events: object

    def show(self) -> None:
        ...

    def bring_to_front(self) -> None:
        ...

    def hide(self) -> None:
        ...

    def destroy(self) -> None:
        ...


class TrayLike(Protocol):
    def stop(self) -> None:
        ...


class DetachedWindowManagerLike(Protocol):
    def close_all(self) -> None:
        ...


class TaskStateLike(Protocol):
    title: str


class ModelManagerControllerApi(Protocol):
    runtime_model_key: str
    runtime_model_loaded: bool
    model_load_running: bool

    def clear_model_status_cache(self) -> None:
        ...

    def mark_runtime_model_pending(self, model_key: str, *, loaded: bool = False, message: str | None = None) -> None:
        ...

    def mark_runtime_model_ready(self, model_key: str | None = None, *, message: str | None = None) -> None:
        ...

    def mark_runtime_model_failed(self, message: str) -> None:
        ...

    def handle_recording_status(self, payload: JsonDict) -> None:
        ...

    def handle_task_message(self, message: str, source: str = "general") -> None:
        ...

    def resolve_model_dir(self) -> str:
        ...

    def normalize_engine_name(self, value: str) -> str:
        ...

    def normalize_model_key(self, value: str) -> str:
        ...

    def is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        ...


class SystemSettingsBridge(Protocol):
    model_manager_controller: ModelManagerControllerApi

    def get_window(self) -> FolderDialogWindow | None:
        ...

    def _resolve_model_dir(self) -> str:
        ...


class StateViewBridge(Protocol):
    def _build_import_ui(self, verify_available: bool = True) -> JsonDict:
        ...

    def _build_runtime_model_state(self) -> JsonDict:
        ...

    def snapshot_live_state(self) -> JsonDict:
        ...

    def get_log_file_name(self) -> str:
        ...

    def get_log_content(self) -> str:
        ...

    def get_detached_config(self, mode: str) -> JsonDict:
        ...

    def _resolve_model_dir(self) -> str:
        ...

    def _resolve_export_dir(self) -> str:
        ...

    def _emit_ui_update(self, sections: Sequence[str]) -> None:
        ...


class StartupBridge(Protocol):
    def set_startup_t0(self, started_at: float) -> None:
        ...

    def _log_startup_marker(self, marker: str) -> None:
        ...

    def bind_window(self, window: FolderDialogWindow) -> None:
        ...

    def get_tray(self) -> TrayLike | None:
        ...

    def bind_tray(self, tray: TrayLike) -> None:
        ...

    def quit_app(self) -> None:
        ...


class StartupWebviewModule(Protocol):
    def create_window(self, *args: object, **kwargs: object) -> FolderDialogWindow:
        ...

    def start(self, callback: Callable[[], None], debug: bool = False) -> None:
        ...


WebviewImporter = Callable[[str], StartupWebviewModule]
FfmpegPathAdder = Callable[..., bool]


class MainWindowBridge(Protocol):
    detached_window_manager: DetachedWindowManagerLike

    def get_window(self) -> FolderDialogWindow | None:
        ...

    def get_tray(self) -> TrayLike | None:
        ...


class ModelManagerBridge(Protocol):
    def reset_task_state(self, title: str) -> None:
        ...

    def update_task_message(self, message: str, source: str = "general") -> None:
        ...

    def update_task_progress(self, value: float, source: str = "general") -> None:
        ...

    def update_task_error(self, error: str) -> None:
        ...

    def finish_task(self, message: str) -> None:
        ...

    def get_settings_snapshot(self) -> JsonDict:
        ...


class RecordingBridge(Protocol):
    _lock: object
    model_manager_controller: ModelManagerControllerApi
    _runtime_model_key: str
    _runtime_model_loaded: bool
    _model_load_running: bool

    def _emit_ui_update(self, sections: Sequence[str]) -> None:
        ...

    def get_settings_snapshot(self) -> JsonDict:
        ...

    def _normalize_engine_name(self, value: str) -> str:
        ...

    def _normalize_model_key(self, value: str) -> str:
        ...

    def bind_headless_main_window(self) -> None:
        ...

    def clear_live(self) -> None:
        ...

    def reset_task_state(self, title: str) -> None:
        ...

    def finish_task(self, message: str) -> None:
        ...

    def update_task_error(self, message: str) -> None:
        ...

    def update_task_message(self, message: str, source: str = "general") -> None:
        ...

    def update_task_progress(self, value: float, source: str = "general") -> None:
        ...


class WhisperLoadApi(Protocol):
    def is_model_bundle_cached(self, *args: object, **kwargs: object) -> bool:
        ...

    def get_model_args(self, settings_snapshot: JsonDict) -> JsonDict:
        ...

    def get_model(self, *args: object, **kwargs: object) -> object:
        ...


WhisperLoadApiGetter = Callable[[], WhisperLoadApi]


class ImportQueueBridge(Protocol):
    _lock: object
    model_manager_controller: ModelManagerControllerApi
    task_state: TaskStateLike
    _model_load_running: bool
    _runtime_model_loaded: bool
    _runtime_model_key: str
    TL_ENGINE_SOURCE_DICT_REF: dict[str, list[str]]
    TL_ENGINE_TARGET_DICT_REF: dict[str, list[str]]

    def _emit_ui_update(self, sections: Sequence[str]) -> None:
        ...

    def _wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        ...

    def get_recording_state(self) -> JsonDict:
        ...

    def get_window(self) -> FolderDialogWindow | None:
        ...

    def _normalize_engine_name(self, value: str) -> str:
        ...

    def _normalize_model_key(self, value: str) -> str:
        ...

    def _resolve_model_dir(self) -> str:
        ...

    def _is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        ...

    def reset_task_state(self, title: str) -> None:
        ...

    def bind_headless_main_window(self) -> None:
        ...

    def clear_live(self) -> None:
        ...

    def finish_task(self, message: str) -> None:
        ...

    def update_task_error(self, message: str) -> None:
        ...

    def update_task_message(self, message: str, source: str = "general") -> None:
        ...

    def update_task_progress(self, value: float, source: str = "general") -> None:
        ...

    def update_task_rows(self, rows: Sequence[Sequence[object]]) -> None:
        ...

    def get_settings_snapshot(self) -> JsonDict:
        ...


class HeadlessDialogFactory(Protocol):
    def __call__(self, master: object, title: str, mode: str, headers: Sequence[str], *, bridge: object) -> object:
        ...


HeadlessMboxFn = Callable[..., object]
ShutdownSeleniumFn = Callable[[], None]
