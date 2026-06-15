from __future__ import annotations

import os
from typing import Any

from speech_translate.controller_protocols import (
    DetachedWindowControllerApi,
    ImportQueueControllerApi,
    MainWindowControllerApi,
    ModelManagerControllerApi,
    RecordingControllerApi,
    StateViewBuilderApi,
    SystemSettingsControllerApi,
)


class ControllerPropertyProxy:
    """Route bridge API properties directly to the owning controller."""

    def __init__(self, controller_attr: str, attr_name: str):
        self.controller_attr = controller_attr
        self.attr_name = attr_name

    def __get__(self, instance: object, owner: type | None = None) -> Any:
        if instance is None:
            return self
        controller = getattr(instance, self.controller_attr)
        return getattr(controller, self.attr_name)

    def __set__(self, instance: object, value: Any) -> None:
        controller = getattr(instance, self.controller_attr)
        setattr(controller, self.attr_name, value)


def _make_controller_forwarder(controller_attr: str, method_name: str):
    def forward(self, *args: object, **kwargs: object):
        controller = getattr(self, controller_attr)
        return getattr(controller, method_name)(*args, **kwargs)

    forward.__name__ = method_name
    return forward


class WebBridgeApiMixin:
    """Expose the pywebview bridge API as explicit controller-backed forwards."""

    model_manager_controller: ModelManagerControllerApi
    import_queue_controller: ImportQueueControllerApi
    recording_controller: RecordingControllerApi
    system_settings_controller: SystemSettingsControllerApi
    state_view_builder: StateViewBuilderApi
    detached_window_controller: DetachedWindowControllerApi
    main_window_controller: MainWindowControllerApi

    _model_status_cache = ControllerPropertyProxy("model_manager_controller", "model_status_cache")
    _model_download_running = ControllerPropertyProxy("model_manager_controller", "model_download_running")
    _model_load_running = ControllerPropertyProxy("model_manager_controller", "model_load_running")
    _runtime_model_key = ControllerPropertyProxy("model_manager_controller", "runtime_model_key")
    _runtime_model_loaded = ControllerPropertyProxy("model_manager_controller", "runtime_model_loaded")
    _runtime_model_message = ControllerPropertyProxy("model_manager_controller", "runtime_model_message")
    _model_manager_engine = ControllerPropertyProxy("model_manager_controller", "model_manager_engine")
    _model_manager_model = ControllerPropertyProxy("model_manager_controller", "model_manager_model")
    _file_import_queue = ControllerPropertyProxy("import_queue_controller", "file_import_queue")
    _processing_queue = ControllerPropertyProxy("import_queue_controller", "processing_queue")
    _record_worker_thread = ControllerPropertyProxy("recording_controller", "record_worker_thread")
    recording_state = ControllerPropertyProxy("recording_controller", "recording_state")

    @staticmethod
    def _path_size(path: str) -> int:
        if not path:
            return 0
        if os.path.isfile(path):
            return os.path.getsize(path)
        if os.path.isdir(path):
            return sum(os.path.getsize(os.path.join(root, file_name)) for root, _, files in os.walk(path) for file_name in files)
        return 0

    @staticmethod
    def _fmt_bytes(value: float) -> str:
        if value <= 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if value < 1024.0:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024.0
        return f"{value:.1f} PB"


_FORWARDED_METHODS: dict[str, tuple[str, str]] = {
    "set_startup_t0": ("main_window_controller", "set_startup_t0"),
    "log_startup_marker": ("main_window_controller", "log_startup_marker"),
    "mark_startup": ("main_window_controller", "mark_startup"),
    "show_main_window": ("main_window_controller", "show_main_window"),
    "save_main_window_geometry": ("main_window_controller", "save_main_window_geometry"),
    "quit_app": ("main_window_controller", "quit_app"),
    "open_directory": ("system_settings_controller", "open_directory"),
    "select_directory": ("system_settings_controller", "select_directory"),
    "open_link": ("system_settings_controller", "open_link"),
    "open_hallucination_filter": ("system_settings_controller", "open_hallucination_filter"),
    "notify": ("system_settings_controller", "notify"),
    "resolve_export_dir": ("system_settings_controller", "resolve_export_dir"),
    "resolve_log_dir": ("system_settings_controller", "resolve_log_dir"),
    "resolve_selenium_chrome_user_data_dir": ("system_settings_controller", "resolve_selenium_chrome_user_data_dir"),
    "get_setting": ("system_settings_controller", "get_setting"),
    "set_setting": ("system_settings_controller", "set_setting"),
    "set_import_setting": ("system_settings_controller", "set_import_setting"),
    "set_record_setting": ("system_settings_controller", "set_record_setting"),
    "get_log_file_name": ("system_settings_controller", "get_log_file_name"),
    "get_log_content": ("system_settings_controller", "get_log_content"),
    "refresh_log": ("system_settings_controller", "refresh_log"),
    "clear_log": ("system_settings_controller", "clear_log"),
    "reload_state": ("state_view_builder", "reload_state"),
    "build_main_ui": ("state_view_builder", "build_main_ui"),
    "build_record_device_ui": ("state_view_builder", "build_record_device_ui"),
    "build_record_ui": ("state_view_builder", "build_record_ui"),
    "build_about": ("state_view_builder", "build_about"),
    "build_audio_source_options": ("state_view_builder", "build_audio_source_options"),
    "get_audio_source_options": ("state_view_builder", "get_audio_source_options"),
    "resolve_model_dir": ("model_manager_controller", "resolve_model_dir"),
    "get_model_manager_keys": ("model_manager_controller", "get_model_manager_keys"),
    "normalize_model_key": ("model_manager_controller", "normalize_model_key"),
    "normalize_engine_name": ("model_manager_controller", "normalize_engine_name"),
    "is_model_available_for_backend": ("model_manager_controller", "is_model_available_for_backend"),
    "verify_model_status": ("model_manager_controller", "verify_model_status"),
    "cache_model_status": ("model_manager_controller", "cache_model_status"),
    "estimate_total_whisper_bytes": ("model_manager_controller", "estimate_total_whisper_bytes"),
    "build_model_manager_state": ("model_manager_controller", "build_model_manager_state"),
    "build_runtime_model_state": ("model_manager_controller", "build_runtime_model_state"),
    "get_model_manager_state": ("model_manager_controller", "get_model_manager_state"),
    "get_runtime_model_state": ("model_manager_controller", "get_runtime_model_state"),
    "check_model": ("model_manager_controller", "check_model"),
    "check_all_models": ("model_manager_controller", "check_all_models"),
    "download_model": ("model_manager_controller", "download_model"),
    "load_runtime_model": ("model_manager_controller", "load_runtime_model"),
    "wait_recording_idle": ("recording_controller", "wait_recording_idle"),
    "set_recording_state": ("recording_controller", "set_recording_state"),
    "get_recording_state": ("recording_controller", "get_recording_state"),
    "start_recording": ("recording_controller", "start_recording"),
    "stop_recording": ("recording_controller", "stop_recording"),
    "get_import_ui_details": ("import_queue_controller", "get_import_ui_details"),
    "build_import_ui": ("import_queue_controller", "build_import_ui"),
    "get_full_display_queue": ("import_queue_controller", "get_full_display_queue"),
    "get_file_processing_state": ("import_queue_controller", "get_file_processing_state"),
    "init_file_batch": ("import_queue_controller", "init_file_batch"),
    "sync_file_status": ("import_queue_controller", "sync_file_status"),
    "add_files_to_import_queue": ("import_queue_controller", "add_files_to_import_queue"),
    "remove_file_from_import_queue": ("import_queue_controller", "remove_file_from_import_queue"),
    "clear_import_queue": ("import_queue_controller", "clear_import_queue"),
    "import_files": ("import_queue_controller", "import_files"),
    "start_import_queue": ("import_queue_controller", "start_import_queue"),
    "stop_import_queue": ("import_queue_controller", "stop_import_queue"),
    "get_detached_config": ("detached_window_controller", "get_detached_config"),
    "set_detached_config": ("detached_window_controller", "set_detached_config"),
    "create_detached_window": ("detached_window_controller", "create_detached_window"),
    "toggle_detached_window": ("detached_window_controller", "toggle_detached_window"),
    "show_detached_window": ("detached_window_controller", "show_detached_window"),
    "hide_detached_window": ("detached_window_controller", "hide_detached_window"),
    "close_detached_window": ("detached_window_controller", "close_detached_window"),
    "update_detached_content": ("detached_window_controller", "update_detached_content"),
    "update_detached_config": ("detached_window_controller", "update_detached_config"),
}


for api_name, (controller_attr, controller_method) in _FORWARDED_METHODS.items():
    setattr(WebBridgeApiMixin, api_name, _make_controller_forwarder(controller_attr, controller_method))


__all__ = [
    "ControllerPropertyProxy",
    "WebBridgeApiMixin",
]
