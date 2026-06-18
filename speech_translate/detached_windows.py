from __future__ import annotations

import json
from pathlib import Path
from time import time
from typing import Callable, Mapping, Optional

from speech_translate.controller_protocols import (
    DetachedWindowManagerBridge,
    JsonDict,
    SettingsStore,
    StartupWebviewModule,
    WebviewWindowLike,
)
from speech_translate.detached_window_api import DetachedWindowApi, RecordingWindowApi
from speech_translate.detached_window_geometry import (
    log_detached_window_loaded_geometry,
    persist_detached_window_placement,
    resolve_detached_window_placement,
)
from speech_translate.detached_window_native import (
    apply_native_window_settings,
    apply_window_topmost,
    build_detached_native_contract,
)
from speech_translate.detached_window_settings import (
    build_detached_window_config,
    build_detached_window_settings,
    detached_setting_key,
    get_detached_live_content,
    normalize_detached_mode,
)
from speech_translate.detached_window_runtime import DetachedWindowDeliveryRuntime
from speech_translate.log_helpers import logger
from speech_translate.webview_runtime import load_webview_runtime
from speech_translate.window_factory import create_preloaded_window
from speech_translate.window_geometry import WindowPlacement
from speech_translate.window_lifecycle import (
    is_preloaded_window,
    reveal_preloaded_window,
    should_skip_preloaded_geometry_save,
)


def build_detached_config(settings_cache: Mapping[str, object], mode: object) -> JsonDict:
    return build_detached_window_config(settings_cache, mode).to_payload()


class DetachedWindowManager:
    """Manages detached subtitle windows using pywebview."""

    def __init__(
        self,
        bridge: DetachedWindowManagerBridge | None = None,
        settings: SettingsStore | None = None,
        webview_loader: Callable[[], StartupWebviewModule] = load_webview_runtime,
    ):
        self.bridge = bridge
        self.settings = settings
        self.webview_loader = webview_loader
        self.runtime = DetachedWindowDeliveryRuntime()
        self.windows: dict[str, WebviewWindowLike] = {}
        self.pending_updates = self.runtime.pending_updates
        self.pending_configs = self.runtime.pending_configs
        self._window_loaded = self.runtime.window_loaded
        self._window_content_ready = self.runtime.window_content_ready
        self._last_content_payload = self.runtime.last_content_payload
        self._last_config_payload = self.runtime.last_config_payload
        self._window_style_cache = self.runtime.window_style_cache
        self._content_sender_busy = self.runtime.content_sender_busy
        self.recording_window: WebviewWindowLike | None = None
        self.pending_recording_payload: JsonDict | None = None
        self._requested_placements: dict[str, WindowPlacement] = {}

    def has_window(self, mode: str) -> bool:
        return normalize_detached_mode(mode) in self.windows

    def get_window(self, mode: str) -> WebviewWindowLike | None:
        return self.windows.get(normalize_detached_mode(mode))

    def move_window(self, mode: str, x: int, y: int) -> bool:
        window = self.get_window(mode)
        if window is None or not hasattr(window, "move"):
            return False
        window.move(x, y)
        return True

    def _flush_pending(self, mode: str, include_content: bool = True) -> None:
        if not self.runtime.is_window_loaded(mode):
            return
        if mode in self.pending_configs:
            self.update_window_config(mode, self.pending_configs[mode])
        if include_content and self.runtime.is_window_content_ready(mode) and mode in self.pending_updates:
            self.update_window_content(mode, self.pending_updates[mode])

    def _is_always_on_top_enabled(self, mode: str) -> bool:
        config = self.runtime.get_pending_config(mode)
        if config is None and self.bridge is not None:
            try:
                config = self.bridge.get_detached_config(mode)
            except Exception:
                config = None
        return bool((config or {}).get("always_on_top", 0))

    def _get_creation_config(self, mode: str) -> JsonDict:
        config = self.runtime.get_pending_config(mode)
        if config is not None:
            return dict(config)
        if self.settings is not None:
            return build_detached_window_settings(self.settings.cache, mode).config.to_payload()
        if self.bridge is not None:
            try:
                return dict(self.bridge.get_detached_config(mode))
            except Exception:
                pass
        return {}

    def _apply_topmost(self, mode: str, focus_nudge: bool = False) -> None:
        logger.debug(f"[DetachedOpen] _apply_topmost enter mode={mode} focus_nudge={focus_nudge}")
        if mode not in self.windows:
            logger.debug(f"[DetachedOpen] _apply_topmost skip missing window mode={mode}")
            return

        enabled = self._is_always_on_top_enabled(mode)
        apply_window_topmost(self.windows.get(mode), enabled=enabled, focus_nudge=focus_nudge)
        logger.debug(f"[DetachedOpen] _apply_topmost done mode={mode} enabled={enabled}")

    def _drop_window_ref(self, mode: str):
        if mode in self.windows:
            self.windows.pop(mode, None)
            self.runtime.drop_window_ref(mode)
            self._requested_placements.pop(mode, None)
            logger.debug(f"Dropped detached window reference: {mode}")

    def _start_content_sender(self, mode: str) -> None:
        if not self.runtime.try_start_content_sender(mode):
            logger.debug(f"[DetachedSend] skip start: sender already busy mode={mode}")
            return
        logger.debug(f"[DetachedSend] sender start mode={mode}")
        try:
            while True:
                if mode not in self.windows:
                    break
                if not self.runtime.is_window_loaded(mode) or not self.runtime.is_window_content_ready(mode):
                    break

                html_content = self.runtime.get_pending_content(mode)
                if html_content is None:
                    logger.debug(f"[DetachedSend] no pending content mode={mode}")
                    break
                if self.runtime.get_last_content_payload(mode) == html_content:
                    break

                try:
                    t0 = time()
                    payload_len = len(str(html_content))
                    logger.debug(f"[DetachedSend] evaluate_js begin mode={mode} payload_len={payload_len}")
                    self.windows[mode].evaluate_js(
                        f"window.postMessage({{type: 'update-content', html: {repr(html_content)}}}, '*')"
                    )
                    self.runtime.note_content_sent(mode, html_content)
                    elapsed_ms = int((time() - t0) * 1000)
                    logger.debug(f"[DetachedSend] evaluate_js done mode={mode} elapsed_ms={elapsed_ms}")
                    logger.debug(f"Updated content for window: {mode}")
                except Exception as exc:
                    logger.error(f"Failed to update window content: {exc}")
                    self._drop_window_ref(mode)
                    break

                if self.runtime.get_pending_content(mode) == html_content:
                    break
        finally:
            self.runtime.stop_content_sender(mode)
            logger.debug(f"[DetachedSend] sender stop mode={mode}")

            if self.runtime.should_restart_content_sender(mode, window_exists=(mode in self.windows)):
                self._start_content_sender(mode)

    def _on_window_closed(self, mode: str) -> None:
        self._drop_window_ref(mode)

    def _on_recording_window_loaded(self) -> None:
        if self.recording_window is None:
            return
        try:
            if is_preloaded_window(self.recording_window):
                reveal_preloaded_window(self.recording_window, bring_to_front=True)
        except Exception:
            logger.exception("Failed to reveal recording popup window")

    def _attach_recording_window_events(self, window: WebviewWindowLike) -> None:
        try:
            if hasattr(window, "events") and hasattr(window.events, "loaded"):
                window.events.loaded += lambda *_: self._on_recording_window_loaded()
            if hasattr(window, "events") and hasattr(window.events, "closed"):
                window.events.closed += lambda *_: setattr(self, "recording_window", None)
        except Exception:
            pass

    def _persist_window_geometry(self, mode: str) -> None:
        window = self.windows.get(mode)
        if should_skip_preloaded_geometry_save(window, show_allowed=False):
            return
        persist_detached_window_placement(self.settings, mode, window)

    def _attach_window_events(self, mode: str, window: WebviewWindowLike) -> None:
        try:
            if hasattr(window, "events") and hasattr(window.events, "closing"):
                window.events.closing += lambda *_: self._persist_window_geometry(mode)
            if hasattr(window, "events") and hasattr(window.events, "closed"):
                window.events.closed += lambda *_: self._on_window_closed(mode)
            if hasattr(window, "events") and hasattr(window.events, "loaded"):
                window.events.loaded += lambda *_: self._on_window_loaded(mode)
        except Exception:
            pass

    def _reveal_window(self, mode: str) -> None:
        window = self.windows.get(mode)
        if window is None:
            return
        if not is_preloaded_window(window):
            return
        try:
            reveal_preloaded_window(window, bring_to_front=False)
            logger.debug(f"[DetachedOpen] revealed detached window mode={mode}")
        except Exception:
            logger.exception(f"[DetachedOpen] failed to reveal detached window mode={mode}")

    def create_window(
        self,
        mode: str = "tc",
        x: Optional[int] = None,
        y: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ):
        mode = normalize_detached_mode(mode)
        if mode in self.windows:
            try:
                self.windows[mode].show()
                try:
                    self.windows[mode].bring_to_front()
                except Exception:
                    pass
                return self.windows[mode]
            except Exception:
                self._drop_window_ref(mode)

        try:
            webview = self.webview_loader()
            html_path = str(Path(__file__).with_name("web") / "detached_window.html")
            create_config = self._get_creation_config(mode)
            always_on_top = bool(create_config.get("always_on_top", 0))
            create_captionless = bool(create_config.get("no_title_bar", 0))
            self.runtime.mark_window_loaded(mode, False)
            self.runtime.mark_window_content_ready(mode, False)
            width, height, x, y = resolve_detached_window_placement(
                self.settings,
                mode,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            self._requested_placements[mode] = WindowPlacement(width=width, height=height, x=x, y=y)
            cache_value = None
            pos_cache = None
            if self.settings is not None:
                cached_settings = build_detached_window_settings(self.settings.cache, mode)
                cache_value = cached_settings.geometry_cache
                pos_cache = cached_settings.position_cache
            logger.info(
                f"[DetachedGeometry][open-created] mode={mode} "
                f"requested={width}x{height} native_request={width}x{height} "
                f"captionless={create_captionless} position={x},{y} cache={cache_value} cache_pos={pos_cache}"
            )
            window_url = f"{html_path}?mode={mode}"
            window = create_preloaded_window(
                webview,
                f"Speech Translate - {'Transcribed' if mode == 'tc' else 'Translated'}",
                window_url,
                js_api=DetachedWindowApi(self),
                placement=self._requested_placements[mode],
                native_contract=build_detached_native_contract(create_config),
                background_color="#060b14",
                transparent=True,
                on_top=always_on_top,
                hidden=False,
                frameless=False,
                easy_drag=True,
            )

            self.windows[mode] = window
            self._attach_window_events(mode, window)
            logger.info(f"Created detached window: {mode}")
            logger.debug(f"[DetachedOpen] before _flush_pending mode={mode}")
            self._flush_pending(mode, include_content=False)
            logger.debug(f"[DetachedOpen] after _flush_pending mode={mode}")
            logger.debug(f"[DetachedOpen] skip _apply_topmost during create mode={mode}")
        except Exception as exc:
            logger.error(f"Failed to create detached window: {exc}")

        logger.debug(f"[DetachedOpen] create_window return mode={mode} exists={mode in self.windows}")
        return self.windows.get(mode)

    def _on_window_loaded(self, mode: str) -> None:
        self.runtime.mark_window_loaded(mode, True)
        self.runtime.mark_window_content_ready(mode, False)
        logger.info(f"[DetachedGeometry][normalize-loaded] mode={mode} ready_for_reveal=true")
        self._flush_pending(mode, include_content=False)

    def mark_window_content_ready(self, mode: str) -> None:
        mode = normalize_detached_mode(mode)
        if mode not in self.windows:
            logger.debug(f"[DetachedReady] ignored: window missing mode={mode}")
            return
        if not self.runtime.is_window_loaded(mode):
            logger.debug(f"[DetachedReady] ignored: not loaded mode={mode}")
            return
        self.runtime.mark_window_content_ready(mode, True)
        logger.info(f"[DetachedReady] content ready mode={mode}")
        if mode in self.pending_configs:
            self.update_window_config(mode, self.pending_configs[mode])
        if mode in self.pending_updates:
            self.update_window_content(mode, self.pending_updates[mode])
        self._reveal_window(mode)
        log_detached_window_loaded_geometry(mode, self.windows.get(mode))

    def show_window(self, mode: str = "tc"):
        mode = normalize_detached_mode(mode)
        if mode in self.windows:
            try:
                if is_preloaded_window(self.windows[mode]):
                    self._reveal_window(mode)
                else:
                    self.windows[mode].show()
                self._apply_topmost(mode, focus_nudge=True)
                logger.info(f"Showed detached window: {mode}")
            except Exception as exc:
                logger.error(f"Failed to show window: {exc}")
                self._drop_window_ref(mode)
                self.create_window(mode)

    def hide_window(self, mode: str = "tc"):
        mode = normalize_detached_mode(mode)
        if mode in self.windows:
            try:
                self.windows[mode].hide()
                logger.info(f"Hidden detached window: {mode}")
            except Exception as exc:
                logger.error(f"Failed to hide window: {exc}")
                self._drop_window_ref(mode)

    def close_window(self, mode: str = "tc"):
        mode = normalize_detached_mode(mode)
        if mode in self.windows:
            try:
                self.windows[mode].destroy()
                logger.info(f"Closed detached window: {mode}")
            except Exception as exc:
                logger.error(f"Failed to close window: {exc}")
                self._drop_window_ref(mode)

    def update_window_content(self, mode: str, html_content: str):
        mode = normalize_detached_mode(mode)
        prev_pending = self.runtime.set_pending_content(mode, html_content)
        if mode in self.windows and self.runtime.is_window_loaded(mode) and self.runtime.is_window_content_ready(mode):
            if self.runtime.should_skip_duplicate_content(mode, html_content, prev_pending):
                return
            self._start_content_sender(mode)
        else:
            logger.debug(
                f"[DetachedSend] deferred mode={mode} has_window={mode in self.windows} "
                f"loaded={self.runtime.is_window_loaded(mode)} ready={self.runtime.is_window_content_ready(mode)}"
            )

    def update_window_config(self, mode: str, config: JsonDict) -> None:
        mode = normalize_detached_mode(mode)
        self.runtime.set_pending_config(mode, config)
        if mode in self.windows and self.runtime.is_window_loaded(mode):
            apply_native_window_settings(self.runtime, mode, self.windows.get(mode), config=config)
            try:
                config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
                if self.runtime.should_skip_config_payload(mode, config_json):
                    return
                self.windows[mode].evaluate_js(
                    f"window.postMessage({{type: 'update-config', config: {config_json}}}, '*')"
                )
                self.runtime.note_config_sent(mode, config_json)
                logger.debug(f"Updated config for window {mode}: {config}")
            except Exception as exc:
                logger.error(f"Failed to update window config: {exc}")
        else:
            logger.debug(
                f"[DetachedConfig] deferred mode={mode} has_window={mode in self.windows} "
                f"loaded={self.runtime.is_window_loaded(mode)} ready={self.runtime.is_window_content_ready(mode)}"
            )

    def close_all(self):
        modes = list(self.windows.keys())
        for mode in modes:
            self.close_window(mode)
        self.close_recording_window()

    def create_recording_window(self, x: int = 180, y: int = 120, width: int = 520, height: int = 340):
        if self.recording_window is not None:
            try:
                if is_preloaded_window(self.recording_window):
                    reveal_preloaded_window(self.recording_window, bring_to_front=True)
                else:
                    self.recording_window.show()
                    try:
                        self.recording_window.bring_to_front()
                    except Exception:
                        pass
            except Exception:
                pass
            return self.recording_window

        try:
            webview = self.webview_loader()
            html_path = str(Path(__file__).with_name("web") / "recording_window.html")
            assert self.bridge is not None
            recording_window_api = RecordingWindowApi(self.bridge.get_recording_state)
            requested = WindowPlacement(width=width, height=height, x=x, y=y)
            self.recording_window = create_preloaded_window(
                webview,
                "Speech Translate - Recording Session",
                html_path,
                js_api=recording_window_api,
                placement=requested,
                background_color="#060b14",
                on_top=True,
                hidden=False,
            )
            self._attach_recording_window_events(self.recording_window)
            logger.info("Created recording popup window")
        except Exception as exc:
            logger.error(f"Failed to create recording popup window: {exc}")

        return self.recording_window

    def close_recording_window(self):
        if self.recording_window is not None:
            try:
                self.recording_window.destroy()
                logger.info("Closed recording popup window")
            except Exception as exc:
                logger.error(f"Failed to close recording popup window: {exc}")
            finally:
                self.recording_window = None

    def update_recording_status(self, payload: JsonDict) -> None:
        self.pending_recording_payload = payload


__all__ = [
    "DetachedWindowApi",
    "DetachedWindowManager",
    "RecordingWindowApi",
    "build_detached_config",
    "detached_setting_key",
    "get_detached_live_content",
    "normalize_detached_mode",
]
