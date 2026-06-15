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


def _get_default_settings_store() -> "SettingsStore":
    from speech_translate.settings_runtime import sj

    return sj

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


BridgeClass = BridgeRuntimeRoot


from speech_translate.settings_runtime import sj

bc = BridgeRuntimeRoot(sj)


__all__ = [
    "BridgeRuntimeRoot",
    "BridgeClass",
    "bc",
    "sj",
]
