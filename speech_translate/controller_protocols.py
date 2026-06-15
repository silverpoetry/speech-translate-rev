from __future__ import annotations

from threading import Thread
from typing import Callable, Mapping, MutableMapping, Protocol, Sequence


JsonDict = dict[str, object]
TaskTableRow = list[object]
TaskTable = list[TaskTableRow]


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


class WebviewWindowLike(Protocol):
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

    def evaluate_js(self, script: str) -> object:
        ...

    def move(self, x: int, y: int) -> None:
        ...


class TrayLike(Protocol):
    def stop(self) -> None:
        ...


class DetachedWindowManagerLike(Protocol):
    def close_all(self) -> None:
        ...


class DetachedWindowManagerApi(Protocol):
    windows: MutableMapping[str, WebviewWindowLike]

    def create_window(self, mode: str, x: int, y: int, width: int, height: int) -> None:
        ...

    def close_window(self, mode: str) -> None:
        ...

    def show_window(self, mode: str) -> None:
        ...

    def hide_window(self, mode: str) -> None:
        ...

    def update_window_content(self, mode: str, html_content: str) -> None:
        ...

    def update_window_config(self, mode: str, config: JsonDict) -> None:
        ...


class TaskStateLike(Protocol):
    title: str


class ModelManagerControllerApi(Protocol):
    model_status_cache: dict[str, JsonDict]
    model_download_running: bool
    runtime_model_key: str
    runtime_model_loaded: bool
    model_load_running: bool
    runtime_model_message: str
    model_manager_engine: str
    model_manager_model: str

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

    def verify_model_status(self, engine: str, model_key: str, model_dir: str) -> tuple[bool, str]:
        ...

    def cache_model_status(
        self,
        engine: str,
        model_key: str,
        downloaded: bool,
        error: str = "",
        downloading: bool = False,
        progress: float | None = None,
        speed: str = "",
    ) -> None:
        ...

    def get_model_manager_keys(self) -> list[str]:
        ...

    def estimate_total_whisper_bytes(self, model_key: str) -> int:
        ...

    def build_model_manager_state(self, engine_hint: str | None = None, include_both: bool = False) -> JsonDict:
        ...

    def build_runtime_model_state(self) -> JsonDict:
        ...

    def get_model_manager_state(self, engine: str | None = None) -> JsonDict:
        ...

    def get_runtime_model_state(self) -> JsonDict:
        ...

    def check_model(self, model_key: str, engine: str = "whisper") -> JsonDict:
        ...

    def check_all_models(self, engine: str = "whisper") -> JsonDict:
        ...

    def download_model(self, model_key: str, engine: str = "whisper") -> JsonDict:
        ...

    def load_runtime_model(self, model_key: str) -> JsonDict:
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


class AppTrayBridge(Protocol):
    def open_directory(self, name: str) -> JsonDict:
        ...

    def get_window(self) -> FolderDialogWindow | None:
        ...

    def quit_app(self) -> None:
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


class DetachedWindowBridge(Protocol):
    def snapshot_live_state(self) -> JsonDict:
        ...


class DetachedWindowManagerBridge(Protocol):
    def get_detached_config(self, mode: str) -> JsonDict:
        ...

    def get_recording_state(self) -> JsonDict:
        ...


class RecordingStateProvider(Protocol):
    def __call__(self) -> JsonDict:
        ...


class DetachedWindowControllerApi(Protocol):
    def get_detached_config(self, mode: str) -> JsonDict:
        ...

    def set_detached_config(self, mode: str, key: str, value: object) -> JsonDict:
        ...

    def create_detached_window(self, mode: str = "tc", x: int | None = None, y: int | None = None) -> JsonDict:
        ...

    def toggle_detached_window(self, mode: str = "tc", x: int | None = None, y: int | None = None) -> JsonDict:
        ...

    def show_detached_window(self, mode: str = "tc") -> JsonDict:
        ...

    def hide_detached_window(self, mode: str = "tc") -> JsonDict:
        ...

    def close_detached_window(self, mode: str = "tc") -> JsonDict:
        ...

    def update_detached_content(self, mode: str, html_content: str) -> JsonDict:
        ...

    def update_detached_config(self, mode: str, config: JsonDict | None = None) -> JsonDict:
        ...


class MainWindowControllerApi(Protocol):
    first_state_logged: bool

    def set_startup_t0(self, started_at: float) -> None:
        ...

    def log_startup_marker(self, marker: str) -> None:
        ...

    def mark_startup(self, marker: str) -> JsonDict:
        ...

    def show_main_window(self) -> None:
        ...

    def save_main_window_geometry(self, force: bool = False) -> None:
        ...

    def quit_app(self) -> None:
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


class StateViewBuilderApi(Protocol):
    def reload_state(self) -> JsonDict:
        ...

    def build_main_ui(self) -> JsonDict:
        ...

    def build_record_device_ui(self, device: str) -> JsonDict:
        ...

    def build_record_ui(self) -> JsonDict:
        ...

    def build_about(self) -> JsonDict:
        ...

    def build_audio_source_options(self, selected_host_api: str | None = None, host_api: str | None = None) -> JsonDict:
        ...

    def get_audio_source_options(self, host_api: str | None = None) -> JsonDict:
        ...


class SystemSettingsControllerApi(Protocol):
    def open_directory(self, name: str) -> dict[str, str]:
        ...

    def select_directory(self, name: str) -> JsonDict:
        ...

    def open_link(self, url: str) -> dict[str, str]:
        ...

    def open_hallucination_filter(self, target: str) -> JsonDict:
        ...

    def notify(self, title: str, message: str) -> dict[str, str]:
        ...

    def resolve_export_dir(self) -> str:
        ...

    def resolve_log_dir(self) -> str:
        ...

    def resolve_selenium_chrome_user_data_dir(self) -> str:
        ...

    def get_setting(self, key: str) -> object | None:
        ...

    def set_setting(self, key: str, value: object) -> JsonDict:
        ...

    def set_import_setting(self, key: str, value: object) -> JsonDict:
        ...

    def set_record_setting(self, key: str, value: object) -> JsonDict:
        ...

    def get_log_file_name(self) -> str:
        ...

    def get_log_content(self) -> str:
        ...

    def refresh_log(self) -> dict[str, str]:
        ...

    def clear_log(self) -> dict[str, str]:
        ...


class RecordingControllerApi(Protocol):
    record_worker_thread: Thread | None
    recording_state: JsonDict

    def wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        ...

    def set_recording_state(self, payload: JsonDict) -> JsonDict:
        ...

    def get_recording_state(self) -> JsonDict:
        ...

    def start_recording(
        self,
        device: str = "mic",
        lang_source: str = "English",
        lang_target: str = "Indonesian",
        engine: str = "Selenium Chrome Translate",
        is_tc: bool = True,
        is_tl: bool = True,
    ) -> JsonDict:
        ...

    def stop_recording(self) -> JsonDict:
        ...


class ImportQueueControllerApi(Protocol):
    file_import_queue: list[object]
    processing_queue: list[JsonDict]

    def get_import_ui_details(self) -> JsonDict:
        ...

    def build_import_ui(self, verify_available: bool = True) -> JsonDict:
        ...

    def get_full_display_queue(self) -> list[JsonDict]:
        ...

    def get_file_processing_state(self) -> JsonDict:
        ...

    def init_file_batch(self, task_name: str, files: list[object]) -> None:
        ...

    def sync_file_status(self, index: int, combined_status: str, is_completed: bool) -> None:
        ...

    def add_files_to_import_queue(self, files: list[str] | None = None) -> JsonDict:
        ...

    def remove_file_from_import_queue(self, index: int | None = None) -> JsonDict:
        ...

    def clear_import_queue(self) -> JsonDict:
        ...

    def import_files(self, files: list[str] | None = None) -> JsonDict:
        ...

    def start_import_queue(self) -> JsonDict:
        ...

    def stop_import_queue(self) -> JsonDict:
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


ShutdownSeleniumFn = Callable[[], None]
