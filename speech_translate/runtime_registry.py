from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from speech_translate.controller_protocols import SettingsStore


def _get_default_bridge_state() -> object:
    from speech_translate.app_runtime import get_runtime_root

    return get_runtime_root()


def _get_default_settings_store() -> SettingsStore:
    from speech_translate.settings_runtime import get_settings_store

    return get_settings_store()


@dataclass
class BridgeStateRegistry:
    state: object | None = None
    state_provider: Callable[[], object] = _get_default_bridge_state

    def get(self) -> object:
        return self.state if self.state is not None else self.state_provider()

    def set(self, state: object) -> None:
        self.state = state

    def clear(self) -> None:
        self.state = None

    @contextmanager
    def override(self, state: object) -> Iterator[object]:
        previous_state = self.state
        self.state = state
        try:
            yield state
        finally:
            self.state = previous_state


@dataclass
class SettingsRegistry:
    settings: SettingsStore | None = None
    settings_provider: Callable[[], SettingsStore] = _get_default_settings_store

    def get(self) -> SettingsStore:
        return self.settings if self.settings is not None else self.settings_provider()

    def set(self, settings: SettingsStore) -> None:
        self.settings = settings

    def clear(self) -> None:
        self.settings = None

    @contextmanager
    def override(self, settings: SettingsStore) -> Iterator[SettingsStore]:
        previous_settings = self.settings
        self.settings = settings
        try:
            yield settings
        finally:
            self.settings = previous_settings


bridge_state_registry = BridgeStateRegistry()
settings_registry = SettingsRegistry()


__all__ = [
    "BridgeStateRegistry",
    "SettingsRegistry",
    "bridge_state_registry",
    "settings_registry",
]
