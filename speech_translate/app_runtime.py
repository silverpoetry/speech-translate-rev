from __future__ import annotations

from platform import system
from typing import TYPE_CHECKING

from speech_translate.bridge_runtime_state import (
    BridgeDownloadRuntime,
    BridgeFileRuntime,
    BridgeLiveTextRuntime,
    BridgeRecordingRuntime,
    BridgeVisualRuntime,
)
from speech_translate.live_text_service import LiveTextRenderer

if TYPE_CHECKING:
    from speech_translate.controller_protocols import SettingsStore
    bc: "BridgeRuntimeRoot"


def _get_default_settings_store() -> "SettingsStore":
    from speech_translate.settings_runtime import get_settings_store

    return get_settings_store()

if system() == "Windows":
    from multiprocessing import Queue
else:
    from .utils.custom.queue import MyQueue as Queue


class BridgeRuntimeRoot:
    """Application runtime root that owns the mutable subsystem state objects."""

    def __init__(self, settings_store: "SettingsStore" | None = None):
        self.visual = BridgeVisualRuntime()
        self.file_runtime = BridgeFileRuntime()
        self.download = BridgeDownloadRuntime()
        self.recording_runtime = BridgeRecordingRuntime(data_queue=Queue())
        self.live_text = BridgeLiveTextRuntime()
        self.live_text_renderer = LiveTextRenderer(settings_store or _get_default_settings_store())


def create_runtime_root(settings_store: "SettingsStore" | None = None) -> BridgeRuntimeRoot:
    return BridgeRuntimeRoot(settings_store)

_runtime_singleton: BridgeRuntimeRoot | None = None


def get_runtime_root() -> BridgeRuntimeRoot:
    global _runtime_singleton
    if _runtime_singleton is None:
        _runtime_singleton = create_runtime_root()
    return _runtime_singleton


def __getattr__(name: str):
    if name == "bc":
        return get_runtime_root()
    if name == "BridgeClass":
        return BridgeRuntimeRoot
    if name == "sj":
        from speech_translate.settings_runtime import get_settings_store

        return get_settings_store()
    raise AttributeError(name)


__all__ = [
    "BridgeRuntimeRoot",
    "create_runtime_root",
    "get_runtime_root",
    "bc",
]
