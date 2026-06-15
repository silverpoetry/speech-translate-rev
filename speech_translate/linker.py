from __future__ import annotations

from speech_translate.app_runtime import BridgeRuntimeRoot, get_runtime_root
from speech_translate.bridge_legacy_facade import BridgeLegacyProxy

class BridgeClass(BridgeLegacyProxy):
    def __init__(self, settings_store=None):
        super().__init__(BridgeRuntimeRoot(settings_store))


_legacy_bridge_singleton: BridgeLegacyProxy | None = None


def get_legacy_bridge() -> BridgeLegacyProxy:
    global _legacy_bridge_singleton
    if _legacy_bridge_singleton is None:
        _legacy_bridge_singleton = BridgeLegacyProxy(get_runtime_root())
    return _legacy_bridge_singleton


def __getattr__(name: str):
    if name == "bc":
        return get_legacy_bridge()
    raise AttributeError(name)

__all__ = [
    "BridgeClass",
    "bc",
]
