from __future__ import annotations

from typing import Optional

from speech_translate.controller_protocols import DetachedWindowBridge, DetachedWindowManagerApi, JsonDict, SettingsStore
from speech_translate.detached_window_geometry import resolve_detached_window_placement
from speech_translate.detached_window_settings import (
    build_detached_window_settings,
    detached_setting_key,
    get_detached_live_content,
    normalize_detached_mode,
)


class DetachedWindowController:
    """Owns detached-window config persistence and high-level window orchestration."""

    def __init__(self, bridge: DetachedWindowBridge, settings: SettingsStore, window_manager: DetachedWindowManagerApi):
        self.bridge = bridge
        self.settings = settings
        self.window_manager = window_manager

    def _normalize_mode(self, mode: str) -> str:
        return normalize_detached_mode(mode)

    def _build_setting_key(self, mode: str, key: str) -> str:
        return detached_setting_key(mode, key)

    def _resolve_window_placement(self, mode: str, x: Optional[int], y: Optional[int]):
        return resolve_detached_window_placement(
            self.settings,
            mode,
            x=x,
            y=y,
            width=None,
            height=None,
        )

    def _push_live_content_if_available(self, mode: str) -> None:
        html = get_detached_live_content(mode, self.bridge.snapshot_live_state())
        if html:
            self.update_detached_content(mode, html)

    def get_detached_config(self, mode: str) -> JsonDict:
        return build_detached_window_settings(self.settings.cache, mode).config.to_payload()

    def set_detached_config(self, mode: str, key: str, value: object) -> JsonDict:
        normalized_mode = self._normalize_mode(mode)
        setting_key = self._build_setting_key(normalized_mode, key)
        self.settings.save_key(setting_key, value)
        return {"key": setting_key, "value": self.settings.cache.get(setting_key)}

    def create_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> JsonDict:
        normalized_mode = self._normalize_mode(mode)
        placement = self._resolve_window_placement(normalized_mode, x, y)
        self.window_manager.create_window(
            normalized_mode,
            placement.x,
            placement.y,
            placement.width,
            placement.height,
        )
        self.update_detached_config(normalized_mode)
        self._push_live_content_if_available(normalized_mode)
        return {"status": "created", "mode": normalized_mode}

    def toggle_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> JsonDict:
        normalized_mode = self._normalize_mode(mode)
        if self.window_manager.has_window(normalized_mode):
            self.window_manager.close_window(normalized_mode)
            return {"status": "closed", "mode": normalized_mode}
        return self.create_detached_window(normalized_mode, x, y)

    def show_detached_window(self, mode: str = "tc") -> JsonDict:
        normalized_mode = self._normalize_mode(mode)
        self.window_manager.show_window(normalized_mode)
        return {"status": "shown", "mode": normalized_mode}

    def hide_detached_window(self, mode: str = "tc") -> JsonDict:
        normalized_mode = self._normalize_mode(mode)
        self.window_manager.hide_window(normalized_mode)
        return {"status": "hidden", "mode": normalized_mode}

    def close_detached_window(self, mode: str = "tc") -> JsonDict:
        normalized_mode = self._normalize_mode(mode)
        self.window_manager.close_window(normalized_mode)
        return {"status": "closed", "mode": normalized_mode}

    def update_detached_content(self, mode: str, html_content: str) -> JsonDict:
        normalized_mode = self._normalize_mode(mode)
        if not self.window_manager.has_window(normalized_mode):
            return {"status": "missing", "mode": normalized_mode}
        self.window_manager.update_window_content(normalized_mode, html_content)
        return {"status": "updated", "mode": normalized_mode}

    def update_detached_config(self, mode: str, config: Optional[JsonDict] = None) -> JsonDict:
        normalized_mode = self._normalize_mode(mode)
        resolved_config = config or self.get_detached_config(normalized_mode)
        self.window_manager.update_window_config(normalized_mode, resolved_config)
        return {"status": "config_updated", "mode": normalized_mode}
