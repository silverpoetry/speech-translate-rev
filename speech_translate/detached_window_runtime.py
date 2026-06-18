from __future__ import annotations

from threading import Lock

from speech_translate.controller_protocols import JsonDict


class DetachedWindowDeliveryRuntime:
    """Owns detached-window content/config delivery state and sender coordination."""

    def __init__(self) -> None:
        self.pending_updates: dict[str, str] = {}
        self.pending_configs: dict[str, JsonDict] = {}
        self.window_loaded: dict[str, bool] = {}
        self.window_content_ready: dict[str, bool] = {}
        self.window_geometry_hint: dict[str, tuple[int, int]] = {}
        self.last_content_payload: dict[str, str] = {}
        self.last_config_payload: dict[str, str] = {}
        self.window_style_cache: dict[str, tuple[int, int]] = {}
        self.content_sender_busy: dict[str, bool] = {}
        self._content_sender_lock = Lock()

    def is_window_loaded(self, mode: str) -> bool:
        return bool(self.window_loaded.get(mode))

    def mark_window_loaded(self, mode: str, loaded: bool) -> None:
        self.window_loaded[mode] = loaded

    def is_window_content_ready(self, mode: str) -> bool:
        return bool(self.window_content_ready.get(mode))

    def mark_window_content_ready(self, mode: str, ready: bool) -> None:
        self.window_content_ready[mode] = ready

    def set_window_geometry_hint(self, mode: str, width: int, height: int) -> None:
        self.window_geometry_hint[mode] = (int(width), int(height))

    def get_window_geometry_hint(self, mode: str) -> tuple[int, int] | None:
        return self.window_geometry_hint.get(mode)

    def set_pending_content(self, mode: str, html_content: str) -> str | None:
        previous = self.pending_updates.get(mode)
        self.pending_updates[mode] = html_content
        return previous

    def get_pending_content(self, mode: str) -> str | None:
        return self.pending_updates.get(mode)

    def get_last_content_payload(self, mode: str) -> str | None:
        return self.last_content_payload.get(mode)

    def note_content_sent(self, mode: str, html_content: str) -> None:
        self.last_content_payload[mode] = html_content

    def should_skip_duplicate_content(self, mode: str, html_content: str, previous_pending: str | None) -> bool:
        return (
            self.last_content_payload.get(mode) == html_content
            and previous_pending == html_content
            and not self.content_sender_busy.get(mode)
        )

    def set_pending_config(self, mode: str, config: JsonDict) -> None:
        self.pending_configs[mode] = config

    def get_pending_config(self, mode: str) -> JsonDict | None:
        return self.pending_configs.get(mode)

    def should_skip_config_payload(self, mode: str, config_json: str) -> bool:
        return self.last_config_payload.get(mode) == config_json

    def note_config_sent(self, mode: str, config_json: str) -> None:
        self.last_config_payload[mode] = config_json

    def cache_window_style(self, mode: str, style: int, ex_style: int) -> None:
        self.window_style_cache[mode] = (style, ex_style)

    def get_cached_window_style(self, mode: str) -> tuple[int, int] | None:
        return self.window_style_cache.get(mode)

    def try_start_content_sender(self, mode: str) -> bool:
        with self._content_sender_lock:
            if self.content_sender_busy.get(mode):
                return False
            self.content_sender_busy[mode] = True
            return True

    def stop_content_sender(self, mode: str) -> None:
        with self._content_sender_lock:
            self.content_sender_busy[mode] = False

    def should_restart_content_sender(self, mode: str, *, window_exists: bool) -> bool:
        return (
            window_exists
            and self.is_window_loaded(mode)
            and self.is_window_content_ready(mode)
            and self.pending_updates.get(mode) != self.last_content_payload.get(mode)
        )

    def drop_window_ref(self, mode: str) -> None:
        self.window_loaded.pop(mode, None)
        self.window_content_ready.pop(mode, None)
        self.window_geometry_hint.pop(mode, None)
        self.content_sender_busy.pop(mode, None)
        self.last_content_payload.pop(mode, None)
        self.last_config_payload.pop(mode, None)
