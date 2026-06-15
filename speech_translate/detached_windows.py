from __future__ import annotations

import ctypes
import json
from pathlib import Path
from platform import system
from time import time
from typing import Callable, Mapping, Optional

from speech_translate.controller_protocols import (
    DetachedWindowManagerBridge,
    JsonDict,
    RecordingStateProvider,
    SettingsStore,
    StartupWebviewModule,
    WebviewWindowLike,
)
from speech_translate.detached_window_runtime import DetachedWindowDeliveryRuntime
from speech_translate.log_helpers import logger
from speech_translate.webview_runtime import load_webview_runtime
from speech_translate.window_geometry import extract_native_window_geometry, resolve_window_placement


DETACHED_WINDOW_MODES = {"tc", "tl"}
DETACHED_WINDOW_DEFAULT_MODE = "tl"


def normalize_detached_mode(mode: object) -> str:
    normalized = str(mode).lower()
    return normalized if normalized in DETACHED_WINDOW_MODES else DETACHED_WINDOW_DEFAULT_MODE


def build_detached_config(settings_cache: Mapping[str, object], mode: object) -> JsonDict:
    normalized_mode = normalize_detached_mode(mode)
    return {
        "font": settings_cache.get(f"tb_ex_{normalized_mode}_font", "Arial"),
        "font_size": settings_cache.get(f"tb_ex_{normalized_mode}_font_size", 13),
        "font_bold": settings_cache.get(f"tb_ex_{normalized_mode}_font_bold", True),
        "font_color": settings_cache.get(f"tb_ex_{normalized_mode}_font_color", "#FFFFFF"),
        "bg_color": settings_cache.get(f"tb_ex_{normalized_mode}_bg_color", "#000000"),
        "always_on_top": settings_cache.get(f"ex_{normalized_mode}_always_on_top", 0),
        "no_title_bar": settings_cache.get(f"ex_{normalized_mode}_no_title_bar", 0),
        "opacity": settings_cache.get(f"ex_{normalized_mode}_opacity", 1.0),
        "click_through": settings_cache.get(f"ex_{normalized_mode}_click_through", 0),
    }


def detached_setting_key(mode: object, key: str) -> str:
    normalized_mode = normalize_detached_mode(mode)
    if key in ("always_on_top", "no_title_bar", "opacity", "click_through"):
        return f"ex_{normalized_mode}_{key}"
    return f"tb_ex_{normalized_mode}_{key}"


def get_detached_live_content(mode: object, live_state: Mapping[str, object]) -> Optional[str]:
    normalized_mode = normalize_detached_mode(mode)
    content_key = "transcribed" if normalized_mode == "tc" else "translated"
    html = live_state.get(f"detached_{content_key}_html")
    text = live_state.get(f"detached_{content_key}_text")
    if html or text:
        return str(html or text)
    return None


class DetachedWindowManager:
    """Manages detached subtitle windows using pywebview."""

    _GWL_STYLE = -16
    _GWL_EXSTYLE = -20
    _WS_CAPTION = 0x00C00000
    _WS_THICKFRAME = 0x00040000
    _WS_MINIMIZEBOX = 0x00020000
    _WS_MAXIMIZEBOX = 0x00010000
    _WS_SYSMENU = 0x00080000
    _WS_BORDER = 0x00800000
    _WS_DLGFRAME = 0x00400000
    _WS_EX_LAYERED = 0x00080000
    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOZORDER = 0x0004
    _SWP_FRAMECHANGED = 0x0020
    _SWP_SHOWWINDOW = 0x0040
    _LWA_ALPHA = 0x00000002

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

    def _get_window_hwnd(self, mode: str) -> Optional[int]:
        window = self.windows.get(mode)
        if window is None:
            return None

        native_window = getattr(window, "native", None)
        if native_window is None:
            return None

        try:
            handle = getattr(native_window, "Handle", None)
            if handle is not None:
                return int(handle.ToInt32())
        except Exception:
            pass

        try:
            return int(getattr(native_window, "handle"))
        except Exception:
            return None

    def _cache_window_style(self, mode: str, hwnd: int) -> None:
        if self.runtime.get_cached_window_style(mode) is not None:
            return

        try:
            style = int(ctypes.windll.user32.GetWindowLongW(hwnd, self._GWL_STYLE))
            ex_style = int(ctypes.windll.user32.GetWindowLongW(hwnd, self._GWL_EXSTYLE))
            self.runtime.cache_window_style(mode, style, ex_style)
        except Exception:
            pass

    def _apply_native_window_settings(self, mode: str, config: Optional[JsonDict] = None) -> None:
        if system() != "Windows":
            return

        hwnd = self._get_window_hwnd(mode)
        if hwnd is None:
            return

        self._cache_window_style(mode, hwnd)
        original = self.runtime.get_cached_window_style(mode)
        if original is None:
            return

        window = self.windows.get(mode)
        if window is None:
            return

        cfg = config or self.runtime.get_pending_config(mode) or {}
        no_title_bar = bool(cfg.get("no_title_bar", 0))
        opacity_raw = cfg.get("opacity", 1.0)
        try:
            opacity = max(0.1, min(1.0, float(opacity_raw)))
        except Exception:
            opacity = 1.0

        try:
            style, ex_style = original
            style = int(style)
            ex_style = int(ex_style)

            if no_title_bar:
                style &= ~(
                    self._WS_CAPTION
                    | self._WS_MINIMIZEBOX
                    | self._WS_MAXIMIZEBOX
                    | self._WS_SYSMENU
                    | self._WS_BORDER
                    | self._WS_DLGFRAME
                )
            if opacity < 0.999:
                ex_style |= self._WS_EX_LAYERED
            else:
                ex_style &= ~self._WS_EX_LAYERED

            ctypes.windll.user32.SetWindowLongW(hwnd, self._GWL_STYLE, style)
            ctypes.windll.user32.SetWindowLongW(hwnd, self._GWL_EXSTYLE, ex_style)

            if opacity < 0.999:
                ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, int(round(opacity * 255)), self._LWA_ALPHA)
            else:
                ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, self._LWA_ALPHA)

            ctypes.windll.user32.SetWindowPos(
                hwnd,
                None,
                0,
                0,
                0,
                0,
                self._SWP_NOMOVE | self._SWP_NOSIZE | self._SWP_NOZORDER | self._SWP_FRAMECHANGED | self._SWP_SHOWWINDOW,
            )
        except Exception as exc:
            logger.error(f"Failed to apply detached window settings for {mode}: {exc}")

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

    def _apply_topmost(self, mode: str, focus_nudge: bool = False) -> None:
        logger.debug(f"[DetachedOpen] _apply_topmost enter mode={mode} focus_nudge={focus_nudge}")
        if mode not in self.windows:
            logger.debug(f"[DetachedOpen] _apply_topmost skip missing window mode={mode}")
            return

        window = self.windows[mode]
        enabled = self._is_always_on_top_enabled(mode)
        applied = False

        try:
            if hasattr(window, "set_on_top"):
                window.set_on_top(enabled)
                applied = True
        except Exception:
            pass

        if not applied:
            try:
                setattr(window, "on_top", enabled)
                applied = True
            except Exception:
                pass

        if enabled and focus_nudge:
            try:
                window.show()
                window.bring_to_front()
            except Exception:
                pass
        logger.debug(f"[DetachedOpen] _apply_topmost done mode={mode} enabled={enabled}")

    def _drop_window_ref(self, mode: str):
        if mode in self.windows:
            self.windows.pop(mode, None)
            self.runtime.drop_window_ref(mode)
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

    def _persist_window_geometry(self, mode: str) -> None:
        if self.settings is None:
            return

        window = self.windows.get(mode)
        if window is None:
            return

        native_window = getattr(window, "native", None)
        if native_window is None:
            return

        native_geometry = extract_native_window_geometry(native_window)
        width = native_geometry.width
        height = native_geometry.height
        raw_width = native_geometry.raw_width
        raw_height = native_geometry.raw_height
        scale_factor = native_geometry.scale_factor
        if width is None or height is None:
            return

        current_outer_w = None
        current_outer_h = None
        try:
            current_outer_w = int(getattr(window, "width"))
            current_outer_h = int(getattr(window, "height"))
        except Exception:
            pass

        if width >= 200 and height >= 80:
            self.settings.save_key(f"ex_{mode}_geometry", f"{width}x{height}")
            logger.info(
                f"[DetachedGeometry][save] mode={mode} "
                f"saved_logical={width}x{height} raw_client={raw_width}x{raw_height} "
                f"scale_factor={scale_factor:.3f} current_outer={current_outer_w}x{current_outer_h}"
            )

    def _on_window_closed(self, mode: str) -> None:
        self._persist_window_geometry(mode)
        self._drop_window_ref(mode)

    def _attach_window_events(self, mode: str, window: WebviewWindowLike) -> None:
        try:
            if hasattr(window, "events") and hasattr(window.events, "closed"):
                window.events.closed += lambda *_: self._on_window_closed(mode)
            if hasattr(window, "events") and hasattr(window.events, "loaded"):
                window.events.loaded += lambda *_: self._on_window_loaded(mode)
        except Exception:
            pass

    def _resolve_window_placement(
        self,
        mode: str,
        *,
        x: Optional[int],
        y: Optional[int],
        width: Optional[int],
        height: Optional[int],
    ) -> tuple[int, int, int, int]:
        geometry_cache = "900x240"
        if self.settings is not None:
            geometry_cache = str(self.settings.cache.get(f"ex_{mode}_geometry", geometry_cache))

        cached_placement = resolve_window_placement(
            geometry_cache,
            900,
            240,
        )
        resolved_width = int(width) if width is not None else cached_placement.width
        resolved_height = int(height) if height is not None else cached_placement.height

        placement = resolve_window_placement(
            f"{resolved_width}x{resolved_height}",
            resolved_width,
            resolved_height,
            x=x,
            y=y,
        )
        return placement.width, placement.height, placement.x, placement.y

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
            always_on_top = self._is_always_on_top_enabled(mode)
            self.runtime.mark_window_loaded(mode, False)
            width, height, x, y = self._resolve_window_placement(
                mode,
                x=x,
                y=y,
                width=width,
                height=height,
            )

            cache_value = None
            if self.settings is not None:
                cache_value = self.settings.cache.get(f"ex_{mode}_geometry", "900x240")
            logger.info(
                f"[DetachedGeometry][open-created] mode={mode} "
                f"requested={width}x{height} position={x},{y} cache={cache_value}"
            )

            window = webview.create_window(
                f"Speech Translate - {'Transcribed' if mode == 'tc' else 'Translated'}",
                f"{html_path}?mode={mode}",
                js_api=DetachedWindowApi(self),
                width=width,
                height=height,
                x=x,
                y=y,
                background_color="#060b14",
                transparent=True,
                on_top=always_on_top,
            )

            self.windows[mode] = window
            self.runtime.mark_window_content_ready(mode, False)
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

        window = self.windows.get(mode)
        if window is not None:
            native_window = getattr(window, "native", None)
            outer_w = None
            outer_h = None
            client_w = None
            client_h = None
            try:
                outer_w = int(getattr(window, "width"))
                outer_h = int(getattr(window, "height"))
            except Exception:
                pass
            try:
                client_size = getattr(native_window, "ClientSize", None)
                if client_size is not None:
                    client_w = int(getattr(client_size, "Width"))
                    client_h = int(getattr(client_size, "Height"))
            except Exception:
                pass

            scale_factor = None
            try:
                scale_factor = float(getattr(native_window, "scale_factor", 1.0) or 1.0)
            except Exception:
                pass

            logger.info(
                f"[DetachedGeometry][open-loaded] mode={mode} "
                f"outer={outer_w}x{outer_h} client={client_w}x{client_h} scale_factor={scale_factor}"
            )

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

    def show_window(self, mode: str = "tc"):
        mode = normalize_detached_mode(mode)
        if mode in self.windows:
            try:
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
            self._apply_native_window_settings(mode, config)
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
            self.recording_window = webview.create_window(
                "Speech Translate - Recording Session",
                html_path,
                js_api=recording_window_api,
                width=width,
                height=height,
                x=x,
                y=y,
                background_color="#060b14",
                on_top=True,
            )
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


class DetachedWindowApi:
    """Minimal JS API for detached subtitle windows."""

    __slots__ = ("_manager",)

    def __init__(self, manager: DetachedWindowManager):
        self._manager = manager

    def move_detached_window(self, mode: str, x: object, y: object) -> JsonDict:
        mode = normalize_detached_mode(mode)
        window = self._manager.windows.get(mode)
        if window is None or not hasattr(window, "move"):
            return {"status": "missing", "mode": mode}

        try:
            target_x = int(round(float(x)))
            target_y = int(round(float(y)))
        except Exception:
            return {"status": "invalid", "mode": mode}

        try:
            window.move(target_x, target_y)
            return {"status": "moved", "mode": mode, "x": target_x, "y": target_y}
        except Exception as exc:
            logger.error(f"Failed to move detached window {mode}: {exc}")
            return {"status": "error", "mode": mode, "error": str(exc)}

    def detached_window_ready(self, mode: str) -> JsonDict:
        mode = normalize_detached_mode(mode)
        self._manager.mark_window_content_ready(mode)
        return {"status": "ready", "mode": mode}


class RecordingWindowApi:
    """Minimal API exposed to the recording popup window."""

    __slots__ = ("_get_recording_state",)

    def __init__(self, get_recording_state: RecordingStateProvider):
        self._get_recording_state = get_recording_state

    def get_recording_state(self) -> JsonDict:
        return self._get_recording_state()
