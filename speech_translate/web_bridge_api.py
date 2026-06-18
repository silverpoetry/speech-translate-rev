from __future__ import annotations

import os
from dataclasses import dataclass

from speech_translate.controller_protocols import (
    DetachedWindowControllerApi,
    ImportQueueControllerApi,
    MainWindowControllerApi,
    ModelManagerControllerApi,
    RecordingControllerApi,
    StateViewBuilderApi,
    SystemSettingsControllerApi,
)


@dataclass(frozen=True)
class ControllerRouteGroup:
    """Declare a controller-backed method group exposed on the bridge."""

    controller_attr: str
    method_names: tuple[str, ...]


def _make_controller_forwarder(controller_attr: str, method_name: str):
    def forward(self, *args: object, **kwargs: object):
        controller = getattr(self, controller_attr)
        return getattr(controller, method_name)(*args, **kwargs)

    forward.__name__ = method_name
    return forward


def _build_controller_routes(groups: tuple[ControllerRouteGroup, ...]) -> dict[str, tuple[str, str]]:
    routes: dict[str, tuple[str, str]] = {}
    for group in groups:
        for method_name in group.method_names:
            if method_name in routes:
                raise RuntimeError(f"Duplicate bridge API route registered: {method_name}")
            routes[method_name] = (group.controller_attr, method_name)
    return routes


CONTROLLER_ROUTE_GROUPS: tuple[ControllerRouteGroup, ...] = (
    ControllerRouteGroup(
        "main_window_controller",
        (
            "set_startup_t0",
            "log_startup_marker",
            "mark_startup",
            "show_main_window",
            "hide_main_window_to_tray",
            "save_main_window_geometry",
            "quit_app",
        ),
    ),
    ControllerRouteGroup(
        "system_settings_controller",
        (
            "open_directory",
            "select_directory",
            "open_link",
            "open_hallucination_filter",
            "notify",
            "resolve_export_dir",
            "resolve_log_dir",
            "resolve_selenium_chrome_user_data_dir",
            "get_setting",
            "set_setting",
            "set_import_setting",
            "set_record_setting",
            "get_log_file_name",
            "get_log_content",
            "refresh_log",
            "clear_log",
        ),
    ),
    ControllerRouteGroup(
        "state_view_builder",
        (
            "reload_state",
            "build_main_ui",
            "build_record_device_ui",
            "build_record_ui",
            "build_about",
            "build_audio_source_options",
            "get_audio_source_options",
        ),
    ),
    ControllerRouteGroup(
        "model_manager_controller",
        (
            "resolve_model_dir",
            "get_model_manager_keys",
            "normalize_model_key",
            "normalize_engine_name",
            "is_model_available_for_backend",
            "verify_model_status",
            "cache_model_status",
            "estimate_total_whisper_bytes",
            "build_model_manager_state",
            "build_runtime_model_state",
            "get_model_manager_state",
            "get_runtime_model_state",
            "check_model",
            "check_all_models",
            "download_model",
            "load_runtime_model",
        ),
    ),
    ControllerRouteGroup(
        "recording_controller",
        (
            "wait_recording_idle",
            "set_recording_state",
            "get_recording_state",
            "rerender_live_text",
            "start_recording",
            "stop_recording",
        ),
    ),
    ControllerRouteGroup(
        "import_queue_controller",
        (
            "get_import_ui_details",
            "build_import_ui",
            "get_full_display_queue",
            "get_file_processing_state",
            "init_file_batch",
            "sync_file_status",
            "add_files_to_import_queue",
            "remove_file_from_import_queue",
            "clear_import_queue",
            "import_files",
            "start_import_queue",
            "stop_import_queue",
        ),
    ),
    ControllerRouteGroup(
        "detached_window_controller",
        (
            "get_detached_config",
            "set_detached_config",
            "create_detached_window",
            "toggle_detached_window",
            "show_detached_window",
            "hide_detached_window",
            "close_detached_window",
            "update_detached_content",
            "update_detached_config",
        ),
    ),
)

CONTROLLER_ROUTES = _build_controller_routes(CONTROLLER_ROUTE_GROUPS)
CONTROLLER_API_NAMES: tuple[str, ...] = tuple(CONTROLLER_ROUTES)

# Methods currently exercised by the webview frontend or detached-window UI.
WEBVIEW_PUBLIC_CONTROLLER_API_NAMES: frozenset[str] = frozenset(
    {
        "add_files_to_import_queue",
        "check_all_models",
        "check_model",
        "clear_import_queue",
        "create_detached_window",
        "download_model",
        "get_audio_source_options",
        "get_detached_config",
        "get_file_processing_state",
        "get_import_ui_details",
        "get_model_manager_state",
        "get_recording_state",
        "rerender_live_text",
        "get_runtime_model_state",
        "hide_main_window_to_tray",
        "import_files",
        "load_runtime_model",
        "mark_startup",
        "open_directory",
        "open_hallucination_filter",
        "open_link",
        "quit_app",
        "refresh_log",
        "remove_file_from_import_queue",
        "save_main_window_geometry",
        "select_directory",
        "set_detached_config",
        "set_import_setting",
        "set_record_setting",
        "set_setting",
        "show_main_window",
        "start_import_queue",
        "start_recording",
        "stop_import_queue",
        "stop_recording",
        "toggle_detached_window",
        "update_detached_config",
        "clear_log",
    }
)

WEBVIEW_PUBLIC_APP_API_NAMES: frozenset[str] = frozenset(
    {
        "get_live_state",
        "get_state",
        "get_task_state",
    }
)

WEBVIEW_PUBLIC_API_NAMES: frozenset[str] = WEBVIEW_PUBLIC_APP_API_NAMES | WEBVIEW_PUBLIC_CONTROLLER_API_NAMES

PUBLIC_CONTROLLER_ROUTES: dict[str, tuple[str, str]] = {
    name: CONTROLLER_ROUTES[name] for name in CONTROLLER_API_NAMES if name in WEBVIEW_PUBLIC_CONTROLLER_API_NAMES
}


class _WebBridgeControllerBaseMixin:
    """Common utilities shared by bridge API mixins."""

    model_manager_controller: ModelManagerControllerApi
    import_queue_controller: ImportQueueControllerApi
    recording_controller: RecordingControllerApi
    system_settings_controller: SystemSettingsControllerApi
    state_view_builder: StateViewBuilderApi
    detached_window_controller: DetachedWindowControllerApi
    main_window_controller: MainWindowControllerApi

    controller_api_names = CONTROLLER_API_NAMES

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


class WebBridgePublicApiMixin(_WebBridgeControllerBaseMixin):
    """Public pywebview API surface consumed by the browser frontend."""

    public_app_api_names = WEBVIEW_PUBLIC_APP_API_NAMES
    public_controller_api_names = tuple(PUBLIC_CONTROLLER_ROUTES)
    public_api_names = tuple(sorted(WEBVIEW_PUBLIC_API_NAMES))


class WebBridgeApiMixin(WebBridgePublicApiMixin):
    """Bridge API surface exposed to the pywebview frontend."""


def _install_controller_routes(target_cls: type, routes: dict[str, tuple[str, str]]) -> None:
    for api_name, (controller_attr, controller_method) in routes.items():
        setattr(target_cls, api_name, _make_controller_forwarder(controller_attr, controller_method))


_install_controller_routes(WebBridgeApiMixin, PUBLIC_CONTROLLER_ROUTES)


__all__ = [
    "CONTROLLER_API_NAMES",
    "CONTROLLER_ROUTE_GROUPS",
    "PUBLIC_CONTROLLER_ROUTES",
    "WEBVIEW_PUBLIC_API_NAMES",
    "WEBVIEW_PUBLIC_APP_API_NAMES",
    "WEBVIEW_PUBLIC_CONTROLLER_API_NAMES",
    "ControllerRouteGroup",
    "WebBridgeApiMixin",
    "WebBridgePublicApiMixin",
]
