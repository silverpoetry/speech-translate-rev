from __future__ import annotations

from dataclasses import dataclass

from speech_translate.linker import bc


@dataclass
class WebBridgeRegistry:
    state: object = bc

    def get(self):
        return getattr(self.state, "web_bridge", None)

    def set(self, bridge) -> None:
        setattr(self.state, "web_bridge", bridge)


web_bridge_registry = WebBridgeRegistry()


__all__ = [
    "WebBridgeRegistry",
    "web_bridge_registry",
]
