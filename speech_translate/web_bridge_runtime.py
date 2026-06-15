from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from speech_translate.runtime_registry import bridge_state_registry

def _get_default_bridge_state() -> object:
    return bridge_state_registry.get()


@dataclass
class WebBridgeRegistry:
    state: object | None = None
    state_provider: Callable[[], object] = _get_default_bridge_state

    def _state(self) -> object:
        return self.state if self.state is not None else self.state_provider()

    def get(self):
        return getattr(self._state(), "web_bridge", None)

    def set(self, bridge) -> None:
        setattr(self._state(), "web_bridge", bridge)


web_bridge_registry = WebBridgeRegistry()


__all__ = [
    "WebBridgeRegistry",
    "web_bridge_registry",
]
