from __future__ import annotations

from threading import Thread
from typing import Optional

from speech_translate.controller_protocols import (
    DetachedWindowControllerApi,
    ImportQueueControllerApi,
    JsonDict,
    MainWindowControllerApi,
    ModelManagerControllerApi,
    RecordingControllerApi,
    StateViewBuilderApi,
    SystemSettingsControllerApi,
)
from speech_translate.model_manager import ModelManagerController


class WebBridgeFacadeMixin:
    """Explicit facade forwarding for WebBridge controller-backed APIs."""

    model_manager_controller: ModelManagerControllerApi
    import_queue_controller: ImportQueueControllerApi
    recording_controller: RecordingControllerApi
    system_settings_controller: SystemSettingsControllerApi
    state_view_builder: StateViewBuilderApi
    detached_window_controller: DetachedWindowControllerApi
    main_window_controller: MainWindowControllerApi

    @property
    def _model_status_cache(self) -> dict[str, JsonDict]:
        return self.model_manager_controller.model_status_cache

    @_model_status_cache.setter
    def _model_status_cache(self, value: dict[str, JsonDict]) -> None:
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
    def _file_import_queue(self) -> list[object]:
        return self.import_queue_controller.file_import_queue

    @_file_import_queue.setter
    def _file_import_queue(self, value: list[object]) -> None:
        self.import_queue_controller.file_import_queue = value

    @property
    def _processing_queue(self) -> list[JsonDict]:
        return self.import_queue_controller.processing_queue

    @_processing_queue.setter
    def _processing_queue(self, value: list[JsonDict]) -> None:
        self.import_queue_controller.processing_queue = value

    @property
    def _record_worker_thread(self) -> Optional[Thread]:
        return self.recording_controller.record_worker_thread

    @_record_worker_thread.setter
    def _record_worker_thread(self, value: Optional[Thread]) -> None:
        self.recording_controller.record_worker_thread = value

    @property
    def recording_state(self) -> JsonDict:
        return self.recording_controller.recording_state

    @recording_state.setter
    def recording_state(self, value: JsonDict) -> None:
        self.recording_controller.recording_state = value

    def set_startup_t0(self, started_at: float) -> None:
        self.main_window_controller.set_startup_t0(started_at)

    def _log_startup_marker(self, marker: str) -> None:
        self.main_window_controller.log_startup_marker(marker)

    def mark_startup(self, marker: str) -> JsonDict:
        return self.main_window_controller.mark_startup(marker)

    def show_main_window(self) -> None:
        self.main_window_controller.show_main_window()

    def _save_main_window_geometry(self, force: bool = False) -> None:
        self.main_window_controller.save_main_window_geometry(force=force)

    def quit_app(self) -> None:
        self.main_window_controller.quit_app()

    def open_directory(self, name: str) -> Dict[str, str]:
        return self.system_settings_controller.open_directory(name)

    def select_directory(self, name: str) -> JsonDict:
        return self.system_settings_controller.select_directory(name)

    def open_link(self, url: str) -> Dict[str, str]:
        return self.system_settings_controller.open_link(url)

    def open_hallucination_filter(self, target: str) -> Dict[str, str]:
        return self.system_settings_controller.open_hallucination_filter(target)

    def notify(self, title: str, message: str) -> Dict[str, str]:
        return self.system_settings_controller.notify(title, message)

    def _resolve_export_dir(self) -> str:
        return self.system_settings_controller.resolve_export_dir()

    def _resolve_log_dir(self) -> str:
        return self.system_settings_controller.resolve_log_dir()

    def _resolve_selenium_chrome_user_data_dir(self) -> str:
        return self.system_settings_controller.resolve_selenium_chrome_user_data_dir()

    def get_setting(self, key: str) -> object | None:
        return self.system_settings_controller.get_setting(key)

    def set_setting(self, key: str, value: object) -> JsonDict:
        return self.system_settings_controller.set_setting(key, value)

    def set_import_setting(self, key: str, value: object) -> JsonDict:
        return self.system_settings_controller.set_import_setting(key, value)

    def set_record_setting(self, key: str, value: object) -> JsonDict:
        return self.system_settings_controller.set_record_setting(key, value)

    def get_log_file_name(self) -> str:
        return self.system_settings_controller.get_log_file_name()

    def get_log_content(self) -> str:
        return self.system_settings_controller.get_log_content()

    def refresh_log(self) -> Dict[str, str]:
        return self.system_settings_controller.refresh_log()

    def clear_log(self) -> Dict[str, str]:
        return self.system_settings_controller.clear_log()

    def reload_state(self) -> JsonDict:
        return self.state_view_builder.reload_state()

    def _build_main_ui(self) -> JsonDict:
        return self.state_view_builder.build_main_ui()

    def _build_record_device_ui(self, device: str) -> JsonDict:
        return self.state_view_builder.build_record_device_ui(device)

    def _build_record_ui(self) -> JsonDict:
        return self.state_view_builder.build_record_ui()

    def _build_about(self) -> JsonDict:
        return self.state_view_builder.build_about()

    def _build_audio_source_options(self, selected_host_api: Optional[str] = None) -> JsonDict:
        return self.state_view_builder.build_audio_source_options(selected_host_api)

    def get_audio_source_options(self, host_api: Optional[str] = None) -> JsonDict:
        return self.state_view_builder.get_audio_source_options(host_api)

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

    def _cache_model_status(
        self,
        engine: str,
        model_key: str,
        downloaded: bool,
        error: str = "",
        downloading: bool = False,
        progress: Optional[float] = None,
        speed: str = "",
    ) -> None:
        self.model_manager_controller.cache_model_status(engine, model_key, downloaded, error, downloading, progress, speed)

    @staticmethod
    def _path_size(path: str) -> int:
        return ModelManagerController.path_size(path)

    @staticmethod
    def _fmt_bytes(value: float) -> str:
        return ModelManagerController.format_bytes(value)

    def _estimate_total_whisper_bytes(self, model_key: str) -> int:
        return self.model_manager_controller.estimate_total_whisper_bytes(model_key)

    def _build_model_manager_state(self, engine_hint: Optional[str] = None, include_both: bool = False) -> JsonDict:
        return self.model_manager_controller.build_model_manager_state(engine_hint, include_both)

    def _build_runtime_model_state(self) -> JsonDict:
        return self.model_manager_controller.build_runtime_model_state()

    def get_model_manager_state(self, engine: Optional[str] = None) -> JsonDict:
        return self.model_manager_controller.get_model_manager_state(engine)

    def get_runtime_model_state(self) -> JsonDict:
        return self.model_manager_controller.get_runtime_model_state()

    def check_model(self, model_key: str, engine: str = "whisper") -> JsonDict:
        return self.model_manager_controller.check_model(model_key, engine)

    def check_all_models(self, engine: str = "whisper") -> JsonDict:
        return self.model_manager_controller.check_all_models(engine)

    def download_model(self, model_key: str, engine: str = "whisper") -> JsonDict:
        return self.model_manager_controller.download_model(model_key, engine)

    def load_runtime_model(self, model_key: str) -> JsonDict:
        return self.model_manager_controller.load_runtime_model(model_key)

    def _wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        return self.recording_controller.wait_recording_idle(timeout_s=timeout_s)

    def set_recording_state(self, payload: JsonDict) -> JsonDict:
        return self.recording_controller.set_recording_state(payload)

    def get_recording_state(self) -> JsonDict:
        return self.recording_controller.get_recording_state()

    def start_recording(
        self,
        device: str = "mic",
        lang_source: str = "English",
        lang_target: str = "Indonesian",
        engine: str = "Selenium Chrome Translate",
        is_tc: bool = True,
        is_tl: bool = True,
    ) -> JsonDict:
        return self.recording_controller.start_recording(device, lang_source, lang_target, engine, is_tc, is_tl)

    def stop_recording(self) -> JsonDict:
        return self.recording_controller.stop_recording()

    def get_import_ui_details(self) -> JsonDict:
        return self.import_queue_controller.get_import_ui_details()

    def _build_import_ui(self, verify_available: bool = True) -> JsonDict:
        return self.import_queue_controller.build_import_ui(verify_available=verify_available)

    def _get_full_display_queue(self) -> list[JsonDict]:
        return self.import_queue_controller.get_full_display_queue()

    def get_file_processing_state(self) -> JsonDict:
        return self.import_queue_controller.get_file_processing_state()

    def init_file_batch(self, task_name: str, files: list[object]) -> None:
        self.import_queue_controller.init_file_batch(task_name, files)

    def sync_file_status(self, index: int, combined_status: str, is_completed: bool) -> None:
        self.import_queue_controller.sync_file_status(index, combined_status, is_completed)

    def add_files_to_import_queue(self, files: Optional[list[str]] = None) -> JsonDict:
        return self.import_queue_controller.add_files_to_import_queue(files)

    def remove_file_from_import_queue(self, index: Optional[int] = None) -> JsonDict:
        return self.import_queue_controller.remove_file_from_import_queue(index)

    def clear_import_queue(self) -> JsonDict:
        return self.import_queue_controller.clear_import_queue()

    def import_files(self, files: Optional[list[str]] = None) -> JsonDict:
        return self.import_queue_controller.import_files(files)

    def start_import_queue(self) -> JsonDict:
        return self.import_queue_controller.start_import_queue()

    def stop_import_queue(self) -> JsonDict:
        return self.import_queue_controller.stop_import_queue()

    def get_detached_config(self, mode: str) -> JsonDict:
        return self.detached_window_controller.get_detached_config(mode)

    def set_detached_config(self, mode: str, key: str, value: object) -> JsonDict:
        return self.detached_window_controller.set_detached_config(mode, key, value)

    def create_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> JsonDict:
        return self.detached_window_controller.create_detached_window(mode, x, y)

    def toggle_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> JsonDict:
        return self.detached_window_controller.toggle_detached_window(mode, x, y)

    def show_detached_window(self, mode: str = "tc") -> JsonDict:
        return self.detached_window_controller.show_detached_window(mode)

    def hide_detached_window(self, mode: str = "tc") -> JsonDict:
        return self.detached_window_controller.hide_detached_window(mode)

    def close_detached_window(self, mode: str = "tc") -> JsonDict:
        return self.detached_window_controller.close_detached_window(mode)

    def update_detached_content(self, mode: str, html_content: str) -> JsonDict:
        return self.detached_window_controller.update_detached_content(mode, html_content)

    def update_detached_config(self, mode: str, config: Optional[JsonDict] = None) -> JsonDict:
        return self.detached_window_controller.update_detached_config(mode, config)
