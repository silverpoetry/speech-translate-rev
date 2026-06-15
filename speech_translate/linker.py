from __future__ import annotations

from speech_translate.app_runtime import BridgeRuntimeRoot, bc as runtime_bc
from speech_translate.bridge_legacy_facade import BridgeLegacyProxy
from speech_translate.bridge_runtime_state import (
    BridgeDownloadRuntime,
    BridgeFileRuntime,
    BridgeLiveTextRuntime,
    BridgeRecordingRuntime,
    BridgeVisualRuntime,
)
from speech_translate.settings_runtime import sj

class BridgeClass(BridgeLegacyProxy):
    def __init__(self, settings_store=None):
        super().__init__(BridgeRuntimeRoot(settings_store))


bc = BridgeLegacyProxy(runtime_bc)

__all__ = [
    "BridgeClass",
    "BridgeRuntimeRoot",
    "BridgeDownloadRuntime",
    "BridgeFileRuntime",
    "BridgeLiveTextRuntime",
    "BridgeRecordingRuntime",
    "BridgeVisualRuntime",
    "bc",
    "sj",
]
