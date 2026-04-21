import os
import ctypes
import re
import subprocess
import sys
from importlib import import_module
from dataclasses import asdict, dataclass
from pathlib import Path
from platform import processor, release, system, version
from signal import SIGINT, signal
from threading import Lock, Thread
from typing import Any, Dict, Optional, List, cast
from time import sleep, strftime, time
from urllib.request import Request, urlopen

from loguru import logger

from speech_translate._constants import APP_NAME
from speech_translate._logging import init_logging
from speech_translate._path import dir_debug, dir_export, dir_log, dir_user, p_app_icon
from speech_translate._version import __version__
from speech_translate.linker import bc, sj
from speech_translate.web_backend import HeadlessFileProcessDialog, WebTaskBridge, headless_mbox
from speech_translate.utils.audio.device import (
    get_default_host_api,
    get_default_input_device,
    get_default_output_device,
    get_host_apis,
    get_input_devices,
    get_output_devices,
)
from speech_translate.utils.helper import native_notify, open_folder, open_url
from speech_translate.utils.whisper.download import (
    get_default_download_root,
    verify_model_faster_whisper,
    verify_model_whisper,
)
from speech_translate.utils.whisper.helper import model_select_dict, model_values
from speech_translate.utils.types import SettingDict
from speech_translate.utils.translate.language import TL_ENGINE_SOURCE_DICT, TL_ENGINE_TARGET_DICT, WHISPER_LANG_LIST
from speech_translate.utils.translate.translator import shutdown_selenium_translator


_whisper_load_api = None


def _get_whisper_load_api():
    global _whisper_load_api
    if _whisper_load_api is None:
        from speech_translate.utils.whisper import load as whisper_load

        _whisper_load_api = whisper_load
    return _whisper_load_api


class NoConsolePopen(subprocess.Popen):
    """Disable console windows when spawning subprocesses on Windows."""

    def __init__(self, args, **kwargs):
        if system() == "Windows" and "startupinfo" not in kwargs:
            kwargs["startupinfo"] = subprocess.STARTUPINFO()
            kwargs["startupinfo"].dwFlags |= subprocess.STARTF_USESHOWWINDOW
        super().__init__(args, **kwargs)


subprocess.Popen = NoConsolePopen


def _parse_window_size(raw_value: Any, default_width: int, default_height: int) -> tuple[int, int]:
    text = str(raw_value or "").strip().lower()
    match = re.match(r"^(\d+)\s*x\s*(\d+)$", text)
    if match:
        width = max(320, int(match.group(1)))
        height = max(180, int(match.group(2)))
        if system() == "Windows":
            try:
                screen_width = int(ctypes.windll.user32.GetSystemMetrics(0))
                screen_height = int(ctypes.windll.user32.GetSystemMetrics(1))
                width = min(width, max(320, screen_width - 80))
                height = min(height, max(180, screen_height - 120))
            except Exception:
                pass
        return width, height
    return default_width, default_height


def _get_virtual_screen_bounds() -> tuple[int, int, int, int]:
    if system() == "Windows":
        try:
            user32 = ctypes.windll.user32
            left = int(user32.GetSystemMetrics(76))
            top = int(user32.GetSystemMetrics(77))
            width = int(user32.GetSystemMetrics(78))
            height = int(user32.GetSystemMetrics(79))
            if width > 0 and height > 0:
                return left, top, width, height
        except Exception:
            pass
    return 0, 0, 1920, 1080


def _center_window_pos(width: int, height: int) -> tuple[int, int]:
    if system() == "Windows":
        try:
            user32 = ctypes.windll.user32
            screen_width = int(user32.GetSystemMetrics(0))
            screen_height = int(user32.GetSystemMetrics(1))

            scale_factor = 1.0
            try:
                scale_factor = float(ctypes.windll.shcore.GetScaleFactorForDevice(0)) / 100.0
                if scale_factor <= 0:
                    scale_factor = 1.0
            except Exception:
                scale_factor = 1.0

            # WinForms applies scale factor to x/y (location) but not to width/height.
            # Center in physical pixels first, then convert location to logical units.
            centered_x_px = max(0, (screen_width - max(1, width)) // 2)
            centered_y_px = max(0, (screen_height - max(1, height)) // 2)
            centered_x = int(round(centered_x_px / scale_factor))
            centered_y = int(round(centered_y_px / scale_factor))
            return centered_x, centered_y
        except Exception:
            pass

    left, top, v_width, v_height = _get_virtual_screen_bounds()
    centered_x = left + max(0, (v_width - max(1, width)) // 2)
    centered_y = top + max(0, (v_height - max(1, height)) // 2)
    return centered_x, centered_y


def _ensure_visible_or_center(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    left, top, v_width, v_height = _get_virtual_screen_bounds()
    right = left + max(1, v_width)
    bottom = top + max(1, v_height)

    visible_left = max(left, x)
    visible_top = max(top, y)
    visible_right = min(right, x + max(1, width))
    visible_bottom = min(bottom, y + max(1, height))
    visible_width = max(0, visible_right - visible_left)
    visible_height = max(0, visible_bottom - visible_top)

    if visible_width >= 120 and visible_height >= 80:
        return x, y

    return _center_window_pos(width, height)


def add_ffmpeg_to_path(weak: bool = False) -> bool:
    """Add ffmpeg executables to PATH."""
    if getattr(sys, "frozen", False):
        from static_ffmpeg import _add_paths, run

        run.sys.stdout = sys.stderr
        if weak:
            has_ffmpeg = _add_paths._has("ffmpeg") is not None
            has_ffprobe = _add_paths._has("ffprobe") is not None
            if has_ffmpeg and has_ffprobe:
                return False

        ffmpeg, _ = run.get_or_fetch_platform_executables_else_raise()
        os.environ["PATH"] = os.pathsep.join([os.path.dirname(ffmpeg), os.environ["PATH"]])
        return True

    from static_ffmpeg import _add_paths

    return _add_paths.add_paths()


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

    def __init__(self, bridge=None):
        self.bridge = bridge
        self.windows = {}  # {mode: window_object}
        self.pending_updates = {}  # {mode: content_html}
        self.pending_configs = {}  # {mode: config_dict}
        self._window_loaded = {}  # {mode: bool}
        self._window_content_ready = {}  # {mode: bool}
        self._last_content_payload = {}  # {mode: html_content}
        self._last_config_payload = {}  # {mode: config_json}
        self._window_style_cache = {}  # {mode: (style, ex_style)}
        self._content_sender_busy = {}  # {mode: bool}
        self._content_sender_lock = Lock()
        self.recording_window = None
        self.pending_recording_payload = None

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
        if mode in self._window_style_cache:
            return

        try:
            style = int(ctypes.windll.user32.GetWindowLongW(hwnd, self._GWL_STYLE))
            ex_style = int(ctypes.windll.user32.GetWindowLongW(hwnd, self._GWL_EXSTYLE))
            self._window_style_cache[mode] = (style, ex_style)
        except Exception:
            pass

    def _apply_native_window_settings(self, mode: str, config: Optional[Dict[str, Any]] = None) -> None:
        if system() != "Windows":
            return

        hwnd = self._get_window_hwnd(mode)
        if hwnd is None:
            return

        self._cache_window_style(mode, hwnd)
        original = self._window_style_cache.get(mode)
        if original is None:
            return

        window = self.windows.get(mode)
        if window is None:
            return

        cfg = config or self.pending_configs.get(mode) or {}
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
        if not self._window_loaded.get(mode):
            return
        if mode in self.pending_configs:
            self.update_window_config(mode, self.pending_configs[mode])
        if include_content and self._window_content_ready.get(mode) and mode in self.pending_updates:
            self.update_window_content(mode, self.pending_updates[mode])

    def _is_always_on_top_enabled(self, mode: str) -> bool:
        config = self.pending_configs.get(mode)
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
            self._window_loaded.pop(mode, None)
            self._window_content_ready.pop(mode, None)
            self._content_sender_busy.pop(mode, None)
            self._last_content_payload.pop(mode, None)
            self._last_config_payload.pop(mode, None)
            logger.debug(f"Dropped detached window reference: {mode}")

    def _start_content_sender(self, mode: str) -> None:
        with self._content_sender_lock:
            if self._content_sender_busy.get(mode):
                logger.debug(f"[DetachedSend] skip start: sender already busy mode={mode}")
                return
            self._content_sender_busy[mode] = True
            logger.debug(f"[DetachedSend] sender start mode={mode}")
        try:
            while True:
                if mode not in self.windows:
                    break
                if not self._window_loaded.get(mode) or not self._window_content_ready.get(mode):
                    break

                html_content = self.pending_updates.get(mode)
                if html_content is None:
                    logger.debug(f"[DetachedSend] no pending content mode={mode}")
                    break
                if self._last_content_payload.get(mode) == html_content:
                    break

                try:
                    t0 = time()
                    payload_len = len(str(html_content))
                    logger.debug(f"[DetachedSend] evaluate_js begin mode={mode} payload_len={payload_len}")
                    self.windows[mode].evaluate_js(
                        f"window.postMessage({{type: 'update-content', html: {repr(html_content)}}}, '*')"
                    )
                    self._last_content_payload[mode] = html_content
                    elapsed_ms = int((time() - t0) * 1000)
                    logger.debug(f"[DetachedSend] evaluate_js done mode={mode} elapsed_ms={elapsed_ms}")
                    logger.debug(f"Updated content for window: {mode}")
                except Exception as e:
                    logger.error(f"Failed to update window content: {e}")
                    self._drop_window_ref(mode)
                    break

                if self.pending_updates.get(mode) == html_content:
                    break
        finally:
            with self._content_sender_lock:
                self._content_sender_busy[mode] = False
                logger.debug(f"[DetachedSend] sender stop mode={mode}")

            if (
                mode in self.windows
                and self._window_loaded.get(mode)
                and self._window_content_ready.get(mode)
                and self.pending_updates.get(mode) != self._last_content_payload.get(mode)
            ):
                self._start_content_sender(mode)

    def _persist_window_geometry(self, mode: str) -> None:
        window = self.windows.get(mode)
        if window is None:
            return

        native_window = getattr(window, "native", None)
        if native_window is None:
            return

        try:
            client_size = getattr(native_window, "ClientSize", None)
            if client_size is None:
                return

            raw_width = int(getattr(client_size, "Width"))
            raw_height = int(getattr(client_size, "Height"))

            scale_factor = float(getattr(native_window, "scale_factor", 1.0) or 1.0)
            if scale_factor <= 0:
                scale_factor = 1.0

            width = int(round(raw_width / scale_factor))
            height = int(round(raw_height / scale_factor))
        except Exception:
            return

        current_outer_w = None
        current_outer_h = None
        try:
            current_outer_w = int(getattr(window, "width"))
            current_outer_h = int(getattr(window, "height"))
        except Exception:
            pass

        if width >= 200 and height >= 80:
            sj.save_key(f"ex_{mode}_geometry", f"{width}x{height}")

            logger.info(
                f"[DetachedGeometry][save] mode={mode} "
                f"saved_logical={width}x{height} raw_client={raw_width}x{raw_height} "
                f"scale_factor={scale_factor:.3f} current_outer={current_outer_w}x{current_outer_h}"
            )

    def _on_window_closed(self, mode: str) -> None:
        self._persist_window_geometry(mode)
        self._drop_window_ref(mode)

    def _attach_window_events(self, mode: str, window) -> None:
        try:
            if hasattr(window, "events") and hasattr(window.events, "closed"):
                window.events.closed += lambda *_: self._on_window_closed(mode)
            if hasattr(window, "events") and hasattr(window.events, "loaded"):
                window.events.loaded += lambda *_: self._on_window_loaded(mode)
        except Exception:
            pass

    def create_window(
        self,
        mode: str = "tc",
        x: Optional[int] = None,
        y: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ):
        """Create a detached subtitle window."""
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
            webview = import_module("webview")
            html_path = str(Path(__file__).with_name("web") / "detached_window.html")
            always_on_top = self._is_always_on_top_enabled(mode)
            self._window_loaded[mode] = False

            if width is None or height is None:
                width, height = _parse_window_size(sj.cache.get(f"ex_{mode}_geometry", "900x240"), 900, 240)

            # Always center detached window when creating a new instance.
            x, y = _center_window_pos(width, height)

            x, y = _ensure_visible_or_center(int(x), int(y), int(width), int(height))

            logger.info(
                f"[DetachedGeometry][open-created] mode={mode} "
                f"requested={width}x{height} position={x},{y} "
                f"cache={sj.cache.get(f'ex_{mode}_geometry', '900x240')}"
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
            self._window_content_ready[mode] = False
            self._attach_window_events(mode, window)

            # If the user closes the window from the title bar, clear cached reference
            # so opening again creates a new instance.
            logger.info(f"Created detached window: {mode}")

            logger.debug(f"[DetachedOpen] before _flush_pending mode={mode}")
            self._flush_pending(mode, include_content=False)
            logger.debug(f"[DetachedOpen] after _flush_pending mode={mode}")

            # Do not force topmost during create; this call can intermittently block
            # while the native detached window is still initializing.
            logger.debug(f"[DetachedOpen] skip _apply_topmost during create mode={mode}")

        except Exception as e:
            logger.error(f"Failed to create detached window: {e}")

        logger.debug(f"[DetachedOpen] create_window return mode={mode} exists={mode in self.windows}")
        return self.windows.get(mode)

    def _on_window_loaded(self, mode: str) -> None:
        self._window_loaded[mode] = True
        self._window_content_ready[mode] = False

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

        # Apply config once the page is loaded; content stays blocked until
        # detached page sends explicit ready callback.
        self._flush_pending(mode, include_content=False)

    def mark_window_content_ready(self, mode: str) -> None:
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            return
        if mode not in self.windows:
            logger.debug(f"[DetachedReady] ignored: window missing mode={mode}")
            return
        if not self._window_loaded.get(mode):
            logger.debug(f"[DetachedReady] ignored: not loaded mode={mode}")
            return
        self._window_content_ready[mode] = True
        logger.info(f"[DetachedReady] content ready mode={mode}")
        if mode in self.pending_configs:
            self.update_window_config(mode, self.pending_configs[mode])
        if mode in self.pending_updates:
            self.update_window_content(mode, self.pending_updates[mode])

    def show_window(self, mode: str = "tc"):
        """Show or bring detached window to front."""
        if mode in self.windows:
            try:
                self.windows[mode].show()
                self._apply_topmost(mode, focus_nudge=True)
                logger.info(f"Showed detached window: {mode}")
            except Exception as e:
                logger.error(f"Failed to show window: {e}")
                self._drop_window_ref(mode)
                self.create_window(mode)

    def hide_window(self, mode: str = "tc"):
        """Hide detached window (keep it running)."""
        if mode in self.windows:
            try:
                self.windows[mode].hide()
                logger.info(f"Hidden detached window: {mode}")
            except Exception as e:
                logger.error(f"Failed to hide window: {e}")
                self._drop_window_ref(mode)

    def close_window(self, mode: str = "tc"):
        """Close detached window."""
        if mode in self.windows:
            try:
                self.windows[mode].destroy()
                logger.info(f"Closed detached window: {mode}")
            except Exception as e:
                logger.error(f"Failed to close window: {e}")
                self._drop_window_ref(mode)

    def update_window_content(self, mode: str, html_content: str):
        """Send HTML content to detached window."""
        prev_pending = self.pending_updates.get(mode)
        self.pending_updates[mode] = html_content
        if mode in self.windows and self._window_loaded.get(mode) and self._window_content_ready.get(mode):
            if (
                self._last_content_payload.get(mode) == html_content
                and prev_pending == html_content
                and not self._content_sender_busy.get(mode)
            ):
                return
            self._start_content_sender(mode)
        else:
            logger.debug(
                f"[DetachedSend] deferred mode={mode} has_window={mode in self.windows} "
                f"loaded={self._window_loaded.get(mode)} ready={self._window_content_ready.get(mode)}"
            )

    def update_window_config(self, mode: str, config: Dict[str, Any]):
        """Send configuration to detached window."""
        self.pending_configs[mode] = config
        if mode in self.windows and self._window_loaded.get(mode):
            self._apply_native_window_settings(mode, config)
            try:
                import json
                config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
                if self._last_config_payload.get(mode) == config_json:
                    return
                self.windows[mode].evaluate_js(
                    f"window.postMessage({{type: 'update-config', config: {config_json}}}, '*')"
                )
                self._last_config_payload[mode] = config_json
                logger.debug(f"Updated config for window {mode}: {config}")
            except Exception as e:
                logger.error(f"Failed to update window config: {e}")
        else:
            logger.debug(
                f"[DetachedConfig] deferred mode={mode} has_window={mode in self.windows} "
                f"loaded={self._window_loaded.get(mode)} ready={self._window_content_ready.get(mode)}"
            )


    def close_all(self):
        """Close all detached windows."""
        modes = list(self.windows.keys())
        for mode in modes:
            self.close_window(mode)
        self.close_recording_window()

    def create_recording_window(self, x: int = 180, y: int = 120, width: int = 520, height: int = 340):
        """Create dedicated recording status window."""
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
            webview = import_module("webview")
            html_path = str(Path(__file__).with_name("web") / "recording_window.html")
            assert self.bridge is not None
            self.recording_window_api = RecordingWindowApi(self.bridge.get_recording_state)
            self.recording_window = webview.create_window(
                "Speech Translate - Recording Session",
                html_path,
                js_api=self.recording_window_api,
                width=width,
                height=height,
                x=x,
                y=y,
                background_color="#060b14",
                on_top=True,
            )
            logger.info("Created recording popup window")
        except Exception as e:
            logger.error(f"Failed to create recording popup window: {e}")

        return self.recording_window

    def close_recording_window(self):
        """Close recording status window if open."""
        if self.recording_window is not None:
            try:
                self.recording_window.destroy()
                logger.info("Closed recording popup window")
            except Exception as e:
                logger.error(f"Failed to close recording popup window: {e}")
            finally:
                self.recording_window = None

    def update_recording_status(self, payload: Dict[str, Any]):
        """Push recording status payload to recording popup window."""
        self.pending_recording_payload = payload


class DetachedWindowApi:
    """Minimal JS API for detached subtitle windows."""

    __slots__ = ("_manager",)

    def __init__(self, manager: DetachedWindowManager):
        self._manager = manager

    def move_detached_window(self, mode: str, x: Any, y: Any) -> Dict[str, Any]:
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"

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

    def detached_window_ready(self, mode: str) -> Dict[str, Any]:
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        self._manager.mark_window_content_ready(mode)
        return {"status": "ready", "mode": mode}


class RecordingWindowApi:
    """Minimal API exposed to the recording popup window."""

    __slots__ = ("_get_recording_state",)

    def __init__(self, get_recording_state):
        self._get_recording_state = get_recording_state

    def get_recording_state(self) -> Dict[str, Any]:
        return self._get_recording_state()


@dataclass

class AppState:
    app_name: str
    version: str
    os_name: str
    os_release: str
    os_version: str
    cpu: str
    settings: Dict[str, Any]
    import_ui: Dict[str, Any]
    main_ui: Dict[str, Any]
    record_ui: Dict[str, Any]
    runtime_model: Dict[str, Any]
    live_ui: Dict[str, Any]
    about: Dict[str, Any]
    log_level: str
    current_log: str
    log_content: str


class WebBridge(WebTaskBridge):
    """
    Bridge exposed to the pywebview frontend.
    Handles all communication between the Web UI and the Python backend.
    """

    def __init__(self):
        super().__init__()
        # --- Lifecycle ---
        self._startup_t0: Optional[float] = None
        self._first_state_logged = False
        self._main_window_show_allowed = False
        self._main_geometry_lock = Lock()
        self._main_geometry_last_saved = ""
        
        # --- Detached Windows ---
        self.detached_window_manager = DetachedWindowManager(self)
        
        # --- Model Management ---
        self._model_status_cache: Dict[str, Dict[str, Any]] = {}
        self._model_download_running = False
        self._model_load_running = False
        self._runtime_model_key = self._normalize_model_key(str(sj.cache.get("model_f_import", "")))
        self._runtime_model_loaded = False
        self._runtime_model_message = "模型未预加载"
        self._model_manager_engine = "whisper"
        self._model_manager_model = "small"
        
        # --- File Batch Processing ---
        self._file_import_queue: List[Any] = []     # 全局全景队列 (容纳所有文件)
        self._processing_queue: List[Dict] = []     # 当前正在处理的局部队列
        
        # --- Realtime Recording ---
        self._record_worker_thread: Optional[Thread] = None
        self.recording_state: Dict[str, Any] = {
            "status": "Idle", "active": False, "device": "-", "lang_source": "-",
            "lang_target": "-", "engine": "-", "mode": "-", "timer": "00:00:00",
            "buffer": "0/0 sec", "sentences": "0",
        }
        
        # --- Audio Devices ---
        self._audio_source_cache: Dict[str, Any] = {
            "host_api_options": [], "mic_options_by_host": {}, "speaker_options_by_host": {},
            "mic_options_all": [], "speaker_options_all": [],
        }
        self._audio_source_cache_ready = False
        self._audio_source_cache_loading = True
        Thread(target=self._prime_audio_source_cache, daemon=True).start()

    # =========================================================================
    # SECTION 1: LIFECYCLE & WINDOW MANAGEMENT
    # =========================================================================

    def set_startup_t0(self, started_at: float) -> None:
        self._startup_t0 = started_at

    def _log_startup_marker(self, marker: str) -> None:
        if self._startup_t0 is None:
            logger.debug(f"[Startup] {marker}")
            return
        elapsed_ms = int((time() - self._startup_t0) * 1000)
        logger.debug(f"[Startup] +{elapsed_ms}ms {marker}")

    def mark_startup(self, marker: str) -> Dict[str, Any]:
        label = str(marker or "").strip() or "unknown"
        self._log_startup_marker(f"js_{label}")
        return {"ok": True, "marker": label}

    def bind_window(self, window):
        super().bind_window(window)
        self._log_startup_marker("bind_window")
        try:
            if hasattr(window, "events"):
                if hasattr(window.events, "shown"):
                    window.events.shown += lambda *_: self._on_main_window_shown(window)
                if hasattr(window.events, "loaded"):
                    window.events.loaded += lambda *_: self._log_startup_marker("main_window_loaded")
                if hasattr(window.events, "closed"):
                    window.events.closed += lambda *_: self._save_main_window_geometry(force=True)
        except Exception:
            pass

    def _on_main_window_shown(self, window) -> None:
        if not self._main_window_show_allowed:
            try: window.hide()
            except Exception: pass
        self._log_startup_marker("main_window_shown")

    def show_main_window(self) -> None:
        self._main_window_show_allowed = True
        window = self.get_window()
        if not window: return
        try: window.show()
        except Exception: return
        try: window.bring_to_front()
        except Exception: pass
        self._log_startup_marker("main_window_shown_after_init")

    def _save_main_window_geometry(self, force: bool = False) -> None:
        window = self.get_window()
        if window is None: return
        native_window = getattr(window, "native", None)
        if native_window is None: return

        width, height, raw_width, raw_height, scale_factor = None, None, None, None, 1.0
        try:
            scale_factor = float(getattr(native_window, "scale_factor", 1.0) or 1.0)
            if scale_factor <= 0: scale_factor = 1.0
        except Exception: pass

        try:
            client_size = getattr(native_window, "ClientSize", None)
            if client_size is not None:
                raw_width = int(getattr(client_size, "Width"))
                raw_height = int(getattr(client_size, "Height"))
                width = int(round(raw_width / scale_factor))
                height = int(round(raw_height / scale_factor))
        except Exception: pass

        if width is None or height is None:
            try:
                width = int(getattr(window, "width"))
                height = int(getattr(window, "height"))
            except Exception: return

        if width >= 600 and height >= 300:
            geometry = f"{width}x{height}"
            with self._main_geometry_lock:
                if not force and geometry == self._main_geometry_last_saved: return
                self._main_geometry_last_saved = geometry
                sj.save_key("mw_size", geometry)
            logger.info(f"[MainGeometry][save] logical={geometry} raw_client={raw_width}x{raw_height} scale_factor={scale_factor:.3f} force={force}")

    def bind_tray(self, tray):
        super().bind_tray(tray)

    def quit_app(self) -> None:
        self.detached_window_manager.close_all()
        if tray := self.get_tray():
            try: tray.stop()
            except Exception: pass
        if window := self.get_window():
            try:
                self._save_main_window_geometry()
                window.destroy()
            except Exception: pass

    def open_directory(self, name: str) -> Dict[str, str]:
        mapping = {"export": self._resolve_export_dir(), "log": self._resolve_log_dir(), "debug": dir_debug, "model": self._resolve_model_dir()}
        if target := mapping.get(name): open_folder(target)
        return {"target": target or ""}

    def select_directory(self, name: str) -> Dict[str, Any]:
        target_map = {
            "export": ("dir_export", self._resolve_export_dir()),
            "model": ("dir_model", self._resolve_model_dir()),
            "selenium_chrome": ("selenium_chrome_user_data_dir", self._resolve_selenium_chrome_user_data_dir()),
        }
        setting_info = target_map.get(str(name or "").strip().lower())
        if not setting_info: return {"ok": False, "message": "Unsupported directory target", "path": ""}
        
        setting_key, default_dir = setting_info
        if not (window := self.get_window()): return {"ok": False, "message": "Window not ready", "path": ""}

        try:
            webview = import_module("webview")
            file_dialog = getattr(getattr(webview, "FileDialog", object), "FOLDER", webview.FOLDER_DIALOG)
            selected = window.create_file_dialog(file_dialog, directory=default_dir)
        except Exception as exc:
            logger.exception(exc)
            return {"ok": False, "message": str(exc), "path": ""}

        if not selected: return {"ok": False, "message": "No folder selected", "path": default_dir}
        selected_path = str(selected[0] if isinstance(selected, (list, tuple)) else selected).strip()
        if not selected_path: return {"ok": False, "message": "No folder selected", "path": default_dir}

        sj.save_key(setting_key, selected_path)
        if setting_key == "dir_model": self._model_status_cache.clear()
        return {"ok": True, "message": "Directory selected", "path": selected_path, "setting": setting_key}

    def open_link(self, url: str) -> Dict[str, str]:
        open_url(url)
        return {"url": url}

    def open_hallucination_filter(self, target: str) -> Dict[str, str]:
        try:
            from speech_translate._path import p_filter_rec, p_filter_file_import
            from speech_translate.utils.whisper.helper import create_hallucination_filter
            path = p_filter_rec if target == "rec" else p_filter_file_import
            if not os.path.exists(path):
                create_hallucination_filter('rec' if target == "rec" else 'file')
            
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except Exception as e:
            logger.exception(e)
            return {"ok": False, "message": str(e)}

    def notify(self, title: str, message: str) -> Dict[str, str]:
        logger.info(f"{title}: {message}")
        return {"title": title, "message": message}

    # =========================================================================
    # SECTION 2: SYSTEM & SETTINGS STATE GENERATION
    # =========================================================================

    def _resolve_export_dir(self) -> str:
        configured = sj.cache.get("dir_export", "auto")
        return configured if configured != "auto" else dir_export

    def _resolve_log_dir(self) -> str:
        configured = sj.cache.get("dir_log", "auto")
        return configured if configured != "auto" else dir_log

    def _resolve_selenium_chrome_user_data_dir(self) -> str:
        configured = str(sj.cache.get("selenium_chrome_user_data_dir", "") or "").strip()
        return configured if configured else str(Path(dir_user) / "selenium_chrome_profile")

    def get_setting(self, key: str) -> Any:
        return sj.cache.get(key)

    def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key == "selenium_settings":
            payload = value if isinstance(value, dict) else {}
            compact = max(0, min(3, int(payload.get("compact_level", 2))))
            z_order_raw = str(payload.get("z_order_mode", "behind-main")).strip().lower()
            z_order = z_order_raw if z_order_raw in {"normal", "behind-main", "bottom"} else "behind-main"
            auto_close = bool(payload.get("auto_close_on_task_done", True))
            chrome_user_data_dir = str(payload.get("chrome_user_data_dir", "")).strip()

            sj.save_key("selenium_compact_level", compact)
            sj.save_key("selenium_z_order_mode", z_order)
            sj.save_key("selenium_auto_close_on_task_done", auto_close)
            sj.save_key("selenium_chrome_user_data_dir", chrome_user_data_dir)

            return {"key": key, "value": {
                "selenium_compact_level": sj.cache.get("selenium_compact_level", compact),
                "selenium_z_order_mode": sj.cache.get("selenium_z_order_mode", z_order),
                "selenium_auto_close_on_task_done": sj.cache.get("selenium_auto_close_on_task_done", auto_close),
                "selenium_chrome_user_data_dir": sj.cache.get("selenium_chrome_user_data_dir", chrome_user_data_dir),
            }}

        if key == "selenium_compact_level":
            value = max(0, min(3, int(value)))
        elif key == "selenium_z_order_mode":
            as_text = str(value).strip().lower()
            value = as_text if as_text in {"normal", "behind-main", "bottom"} else "behind-main"
        elif key == "selenium_auto_close_on_task_done":
            value = bool(value)
        elif key == "selenium_chrome_user_data_dir":
            value = str(value or "").strip()

        sj.save_key(key, value)
        if key == "log_level":
            from speech_translate._logging import change_log_level
            change_log_level(str(value))
        return {"key": key, "value": sj.cache.get(key)}

    def set_import_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key in {"model_f_import", "model_mw"}:
            value = value if value in model_select_dict else value
        sj.save_key(key, value)
        return {"key": key, "value": sj.cache.get(key)}

    def set_record_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key == "model_device_preference":
            normalized = str(value or "auto").strip().lower()
            value = normalized if value in {"auto", "cpu", "cuda"} else "auto"
        sj.save_key(key, value)
        return {"key": key, "value": sj.cache.get(key)}

    def get_log_file_name(self) -> str:
        from speech_translate._logging import current_log
        return current_log

    def get_log_content(self) -> str:
        from speech_translate._logging import current_log
        log_path = Path(dir_log) / current_log
        try: content = log_path.read_text(encoding="utf-8")
        except FileNotFoundError: return f"Log file not found: {log_path}"
        except Exception as exc:
            logger.exception(exc)
            return f"Failed to read log file: {exc}"

        return content[-200000:] if len(content) > 200000 else content

    def refresh_log(self) -> Dict[str, str]:
        return {"content": self.get_log_content(), "file": self.get_log_file_name()}

    def clear_log(self) -> Dict[str, str]:
        from speech_translate._logging import clear_current_log_file
        clear_current_log_file()
        logger.info("Log cleared from web UI")
        return self.refresh_log()

    def get_state(self) -> Dict[str, Any]:
        state_t0 = time()
        settings = dict(sj.cache)
        t_settings = time()
        
        compact_settings = {
            "theme": settings.get("theme"), "log_level": settings.get("log_level"), "dir_export": settings.get("dir_export"),
            "dir_model": settings.get("dir_model"), "export_to": settings.get("export_to"), "source_lang_mw": settings.get("source_lang_mw"),
            "target_lang_mw": settings.get("target_lang_mw"), "input": settings.get("input"), "tl_engine_mw": settings.get("tl_engine_mw"),
            "transcribe_mw": settings.get("transcribe_mw", True), "translate_mw": settings.get("translate_mw", True),
            "auto_scroll_log": settings.get("auto_scroll_log"), "auto_refresh_log": settings.get("auto_refresh_log"),
            "source_lang_f_import": settings.get("source_lang_f_import"), "target_lang_f_import": settings.get("target_lang_f_import"),
            "transcribe_f_import": settings.get("transcribe_f_import"), "translate_f_import": settings.get("translate_f_import"),
            "tl_engine_f_import": settings.get("tl_engine_f_import"), "model_f_import": settings.get("model_f_import"),
            "selenium_compact_level": settings.get("selenium_compact_level", 2), "selenium_z_order_mode": settings.get("selenium_z_order_mode", "behind-main"),
            "selenium_auto_close_on_task_done": settings.get("selenium_auto_close_on_task_done", True), "selenium_chrome_user_data_dir": settings.get("selenium_chrome_user_data_dir", ""),
            "enable_initial_prompt": settings.get("enable_initial_prompt", False), "initial_prompts_map": settings.get("initial_prompts_map", {}),
            "condition_on_previous_text": settings.get("condition_on_previous_text", True),
            "filter_rec": settings.get("filter_rec", True), "filter_rec_case_sensitive": settings.get("filter_rec_case_sensitive", False),
            "filter_rec_strip": settings.get("filter_rec_strip", True), "filter_rec_ignore_punctuations": settings.get("filter_rec_ignore_punctuations", "\"',.?!"),
            "filter_rec_exact_match": settings.get("filter_rec_exact_match", False), "filter_rec_similarity": settings.get("filter_rec_similarity", 0.75),
            "filter_file_import": settings.get("filter_file_import", True), "filter_file_import_case_sensitive": settings.get("filter_file_import_case_sensitive", False),
            "filter_file_import_strip": settings.get("filter_file_import_strip", True), "filter_file_import_ignore_punctuations": settings.get("filter_file_import_ignore_punctuations", "\"',.?!"),
            "filter_file_import_exact_match": settings.get("filter_file_import_exact_match", False), "filter_file_import_similarity": settings.get("filter_file_import_similarity", 0.75),
        }

        import_ui, t_import = self._build_import_ui(verify_available=False), time()
        main_ui, t_main = self._build_main_ui(), time()
        record_ui, t_record = self._build_record_ui(), time()
        runtime_model, t_runtime = self._build_runtime_model_state(), time()
        live_ui, t_live = self.snapshot_live_state(), time()
        about, t_about = self._build_about(), time()
        current_log, log_content, t_log = self.get_log_file_name(), self.get_log_content(), time()

        result = asdict(AppState(
            app_name=APP_NAME, version=__version__, os_name=system(), os_release=release(),
            os_version=version(), cpu=processor(), settings=compact_settings, import_ui=import_ui,
            main_ui=main_ui, record_ui=record_ui, runtime_model=runtime_model, live_ui=live_ui,
            about=about, log_level=sj.cache.get("log_level", "DEBUG"), current_log=current_log, log_content=log_content,
        ))
        result["detached_config"] = {"tc": self.get_detached_config("tc"), "tl": self.get_detached_config("tl")}

        if not self._first_state_logged:
            self._first_state_logged = True
            self._log_startup_marker("first_get_state")
        return result

    def reload_state(self) -> Dict[str, Any]:
        return self.get_state()

    def get_task_state(self) -> Dict[str, Any]:
        return self.snapshot_task_state()

    def get_live_state(self) -> Dict[str, Any]:
        return self.snapshot_live_state()

    def _build_main_ui(self) -> Dict[str, Any]:
        s = dict(sj.cache)
        return {
            "input_options": ["mic", "speaker"], "source_options": WHISPER_LANG_LIST, "target_options": WHISPER_LANG_LIST,
            "engine_options": ["Selenium Chrome Translate", "Google Translate", "MyMemoryTranslator", "LibreTranslate"],
            "selected_input": s.get("input"), "selected_source": s.get("source_lang_mw"), "selected_target": s.get("target_lang_mw"),
            "selected_engine": s.get("tl_engine_mw"), "transcribe": s.get("transcribe_mw", True), "translate": s.get("translate_mw", True),
            "auto_scroll_log": s.get("auto_scroll_log"), "auto_refresh_log": s.get("auto_refresh_log"),
        }

    def _build_record_device_ui(self, device: str) -> Dict[str, Any]:
        s = dict(sj.cache)
        return {
            "sample_rate": s.get(f"sample_rate_{device}"), "chunk_size": s.get(f"chunk_size_{device}"), "channels": s.get(f"channels_{device}"),
            "auto_sample_rate": s.get(f"auto_sample_rate_{device}"), "auto_channels": s.get(f"auto_channels_{device}"),
            "min_input": s.get(f"min_input_length_{device}"), "max_buffer": s.get(f"max_buffer_{device}"), "max_sentences": s.get(f"max_sentences_{device}"),
            "no_limit": s.get(f"{device}_no_limit"), "threshold_enable": s.get(f"threshold_enable_{device}"), "threshold_auto": s.get(f"threshold_auto_{device}"),
            "auto_break_buffer": s.get(f"auto_break_buffer_{device}"), "threshold_auto_level": s.get(f"threshold_auto_level_{device}"),
            "threshold_auto_silero": s.get(f"threshold_auto_silero_{device}"), "threshold_silero_min": s.get(f"threshold_silero_{device}_min"),
            "threshold_db": s.get(f"threshold_db_{device}"),
        }

    def _build_record_ui(self) -> Dict[str, Any]:
        s = dict(sj.cache)
        audio_sources = self._build_audio_source_options()
        return {
            "input": s.get("input"), "host_api": s.get("hostAPI"), "mic": s.get("mic"), "speaker": s.get("speaker"),
            "host_api_options": audio_sources.get("host_api_options", []), "mic_options": audio_sources.get("mic_options", []),
            "speaker_options": audio_sources.get("speaker_options", []), "verbose_record": s.get("verbose_record"),
            "transcribe_rate": s.get("transcribe_rate"), "model_device_preference": s.get("model_device_preference", "auto"),
            "model_device_options": ["auto", "cpu", "cuda"], "separate_with": s.get("separate_with"),
            "use_temp": s.get("use_temp"), "keep_temp": s.get("keep_temp"), "file_use_official_whisper": s.get("file_use_official_whisper", False),
            "show_audio_visualizer_in_setting": s.get("show_audio_visualizer_in_setting"),
            "mic_device": self._build_record_device_ui("mic"), "speaker_device": self._build_record_device_ui("speaker"),
        }

    def _build_about(self) -> Dict[str, Any]:
        return {
            "name": APP_NAME, "version": __version__, "os": f"{system()} {release()} {version()}", "cpu": processor(),
            "log_file": self.get_log_file_name(), "model_dir": self._resolve_model_dir(), "export_dir": self._resolve_export_dir(),
        }

    # =========================================================================
    # SECTION 3: AUDIO DEVICE SCANNING
    # =========================================================================

    def _prime_audio_source_cache(self) -> None:
        try:
            host_api_options = get_host_apis()
            mic_options_all = get_input_devices("")
            speaker_options_all = get_output_devices("")

            ok_host, host_info = get_default_host_api()
            default_host_api = str(host_info.get("name", "")) if ok_host and isinstance(host_info, dict) else ""

            def find_default(device_info, all_options):
                if not device_info or not isinstance(device_info, dict): return ""
                name = str(device_info.get("name", ""))
                return next((str(item) for item in all_options if isinstance(item, str) and "[ID:" in item and name.lower() in item.lower()), "") if name else ""

            default_mic = find_default(get_default_input_device()[1], mic_options_all)
            default_speaker = find_default(get_default_output_device()[1], speaker_options_all)

            mic_options_by_host, speaker_options_by_host = {}, {}
            for host_api in host_api_options:
                if isinstance(host_api, str) and not host_api.startswith("["):
                    mic_options_by_host[host_api] = get_input_devices(str(host_api))
                    speaker_options_by_host[host_api] = get_output_devices(str(host_api))

            self._audio_source_cache = {
                "host_api_options": host_api_options, "mic_options_by_host": mic_options_by_host,
                "speaker_options_by_host": speaker_options_by_host, "mic_options_all": mic_options_all,
                "speaker_options_all": speaker_options_all, "default_host_api": default_host_api,
                "default_mic": default_mic, "default_speaker": default_speaker,
            }
        except Exception as exc:
            logger.exception(exc)
            self._audio_source_cache = {
                "host_api_options": [], "mic_options_by_host": {}, "speaker_options_by_host": {},
                "mic_options_all": ["[ERROR] Failed to load input devices"], "speaker_options_all": ["[ERROR] Failed to load output devices"],
                "default_host_api": "", "default_mic": "", "default_speaker": "",
            }
        finally:
            self._audio_source_cache_loading = False
            self._audio_source_cache_ready = True
            try: self._emit_ui_update(["state"])
            except Exception: pass

    def _build_audio_source_options(self, selected_host_api: Optional[str] = None) -> Dict[str, Any]:
        s = dict(sj.cache)
        host_api = str(selected_host_api if selected_host_api is not None else s.get("hostAPI", ""))
        host_api_options = self._audio_source_cache.get("host_api_options", [])
        
        if not host_api or host_api not in host_api_options:
            host_api = str(self._audio_source_cache.get("default_host_api", "")) or str(next((x for x in host_api_options if isinstance(x, str) and not x.startswith("[")), ""))

        if host_api:
            mic_options = self._audio_source_cache.get("mic_options_by_host", {}).get(host_api) or []
            speaker_options = self._audio_source_cache.get("speaker_options_by_host", {}).get(host_api) or []
        else:
            mic_options = self._audio_source_cache.get("mic_options_all", [])
            speaker_options = self._audio_source_cache.get("speaker_options_all", [])

        selected_mic, selected_speaker = s.get("mic"), s.get("speaker")
        if selected_mic not in mic_options:
            selected_mic = self._audio_source_cache.get("default_mic", "") if self._audio_source_cache.get("default_mic", "") in mic_options else (mic_options[0] if mic_options else "")
        if selected_speaker not in speaker_options:
            selected_speaker = self._audio_source_cache.get("default_speaker", "") if self._audio_source_cache.get("default_speaker", "") in speaker_options else (speaker_options[0] if speaker_options else "")

        return {
            "host_api_options": host_api_options, "mic_options": mic_options, "speaker_options": speaker_options,
            "selected_host_api": host_api, "selected_mic": selected_mic, "selected_speaker": selected_speaker,
        }

    def get_audio_source_options(self, host_api: Optional[str] = None) -> Dict[str, Any]:
        return self._build_audio_source_options(host_api)

    # =========================================================================
    # SECTION 4: MODEL MANAGEMENT
    # =========================================================================

    def _resolve_model_dir(self) -> str:
        configured = sj.cache.get("dir_model", "auto")
        return configured if configured != "auto" else get_default_download_root()

    def _get_model_manager_keys(self) -> list[str]:
        base_models = ["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3"]
        return [m if "large" in m else f"{m}.en" for m in base_models] + base_models # Returns both .en and standard

    def _normalize_model_key(self, value: str) -> str:
        if value in model_select_dict: return model_select_dict[value]
        if value in model_values: return value
        for display_name, model_key in model_select_dict.items():
            if model_key == value: return model_key
        return value

    def _normalize_engine_name(self, value: str) -> str:
        return value

    def _is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        if backend == "faster-whisper":
            try: return verify_model_faster_whisper(model_key, model_dir)
            except Exception: return False
        return os.path.exists(os.path.join(model_dir, f"{model_key}.pt"))

    def _verify_model_status(self, engine: str, model_key: str, model_dir: str) -> tuple[bool, str]:
        try:
            return (verify_model_faster_whisper(model_key, model_dir) if engine == "faster-whisper" else verify_model_whisper(model_key, model_dir)), ""
        except Exception as exc:
            return False, str(exc)

    def _cache_model_status(self, engine: str, model_key: str, downloaded: bool, error: str = "", downloading: bool = False, progress: Optional[float] = None, speed: str = "") -> None:
        if progress is None: progress = 100.0 if downloaded else 0.0
        self._model_status_cache[f"{engine}:{model_key}"] = {
            "engine": engine, "model": model_key, "downloaded": downloaded, "error": error,
            "downloading": downloading, "progress": float(max(0.0, min(100.0, progress))), "speed": speed,
        }

    @staticmethod
    def _path_size(path: str) -> int:
        if not path: return 0
        if os.path.isfile(path): return os.path.getsize(path)
        if os.path.isdir(path): return sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(path) for f in files)
        return 0

    @staticmethod
    def _fmt_bytes(value: float) -> str:
        if value <= 0: return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if value < 1024.0: return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024.0
        return f"{value:.1f} PB"

    def _estimate_total_whisper_bytes(self, model_key: str) -> int:
        try:
            from whisper import _MODELS
            if url := _MODELS.get(model_key):
                with urlopen(Request(url, method="HEAD"), timeout=6) as resp:
                    return int(resp.headers.get("Content-Length", 0))
        except Exception: pass
        return 0

    def _build_model_manager_state(self, engine_hint: Optional[str] = None, include_both: bool = False) -> Dict[str, Any]:
        self._model_manager_engine = str(engine_hint or self._model_manager_engine or "whisper")
        if self._model_manager_engine not in {"whisper", "faster-whisper"}: self._model_manager_engine = "whisper"
        
        models = self._get_model_manager_keys()
        self._model_manager_model = str(self._model_manager_model or "small")
        if self._model_manager_model not in models: self._model_manager_model = "small"

        rows = []
        for row_engine in (["whisper", "faster-whisper"] if include_both else [self._model_manager_engine]):
            for m_key in models:
                cached = self._model_status_cache.get(f"{row_engine}:{m_key}")
                rows.append({
                    "model": m_key, "engine": row_engine, "downloaded": cached.get("downloaded") if cached else None,
                    "downloading": cached.get("downloading", False) if cached else False,
                    "progress": float(cached.get("progress", 0.0)) if cached else 0.0,
                    "speed": str(cached.get("speed", "")) if cached else "",
                    "error": cached.get("error", "") if cached else "",
                })

        return {
            "engine_options": ["whisper", "faster-whisper"], "model_options": models,
            "selected_engine": self._model_manager_engine, "selected_model": self._model_manager_model,
            "model_dir": self._resolve_model_dir(), "download_running": self._model_download_running,
            "view_scope": "both" if include_both else "selected", "rows": rows,
        }

    def _build_runtime_model_state(self) -> Dict[str, Any]:
        loaded = bool(self._runtime_model_loaded)
        return {"key": self._runtime_model_key, "loading": bool(self._model_load_running) and not loaded, "loaded": loaded, "message": self._runtime_model_message}

    def get_model_manager_state(self, engine: Optional[str] = None) -> Dict[str, Any]:
        if engine is not None: self._model_manager_engine = str(engine)
        return self._build_model_manager_state(engine)

    def get_runtime_model_state(self) -> Dict[str, Any]:
        return self._build_runtime_model_state()

    def check_model(self, model_key: str, engine: str = "whisper") -> Dict[str, Any]:
        engine = engine.strip().lower()
        self._model_manager_engine = engine if engine in {"whisper", "faster-whisper"} else "whisper"
        self._model_manager_model = model_key

        downloaded, error = self._verify_model_status(self._model_manager_engine, model_key, self._resolve_model_dir())
        self._cache_model_status(self._model_manager_engine, model_key, downloaded, error, downloading=False)
        state = self._build_model_manager_state(self._model_manager_engine)
        state["checked"] = {"model": model_key, "engine": self._model_manager_engine, "downloaded": downloaded, "error": error}
        return state

    def check_all_models(self, engine: str = "whisper") -> Dict[str, Any]:
        engine = engine.strip().lower()
        if engine not in {"whisper", "faster-whisper", "both"}: engine = "whisper"
        if engine != "both": self._model_manager_engine = engine

        model_dir = self._resolve_model_dir()
        for target_engine in (["whisper", "faster-whisper"] if engine == "both" else [engine]):
            for m_key in self._get_model_manager_keys():
                dl, err = self._verify_model_status(target_engine, m_key, model_dir)
                self._cache_model_status(target_engine, m_key, dl, err, downloading=False)

        return self._build_model_manager_state(self._model_manager_engine, include_both=(engine == "both"))

    def download_model(self, model_key: str, engine: str = "whisper") -> Dict[str, Any]:
        engine = engine.strip().lower()
        engine = engine if engine in {"whisper", "faster-whisper"} else "whisper"
        if self._model_download_running: return {"ok": False, "message": "Another download is running"}

        self._model_manager_engine = engine
        self._model_manager_model = model_key

        def worker():
            self._model_download_running = True
            try:
                model_dir = self._resolve_model_dir()
                os.makedirs(model_dir, exist_ok=True)
                self.reset_task_state("Model Download")
                self.update_task_message(f"Preparing download for {model_key} ({engine})", source="model-download")
                self.update_task_progress(5, source="model-download")

                if engine == "whisper":
                    from whisper import _MODELS
                    if not (url := _MODELS.get(model_key)): raise ValueError(f"Invalid model key: {model_key}")
                    observe_path = os.path.join(model_dir, os.path.basename(url))
                    total_bytes = self._estimate_total_whisper_bytes(model_key)
                else:
                    from faster_whisper.utils import _MODELS as FW_MODELS
                    from huggingface_hub.file_download import repo_folder_name
                    if not (repo_id := FW_MODELS.get(model_key)): raise ValueError(f"Invalid model key: {model_key}")
                    observe_path = os.path.join(model_dir, repo_folder_name(repo_id=repo_id, repo_type="model"))
                    try:
                        self.update_task_message(f"Fetching model info for {model_key}...", source="model-download")
                        import huggingface_hub
                        api = huggingface_hub.HfApi()
                        repo_info = api.repo_info(repo_id=repo_id, repo_type="model", files_metadata=True)
                        allow_patterns = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
                        filtered = list(huggingface_hub.utils.filter_repo_objects(
                            items=[f.rfilename for f in repo_info.siblings],
                            allow_patterns=allow_patterns,
                        ))
                        total_bytes = sum(f.size for f in repo_info.siblings if f.rfilename in filtered and f.size is not None)
                    except Exception as e:
                        logger.warning(f"Failed to fetch total size: {e}")
                        total_bytes = 0

                self._cache_model_status(engine, model_key, False, downloading=True, progress=5, speed="-")
                result_box = {"ok": False, "error": None}

                def _do_download():
                    try:
                        if engine == "whisper":
                            from whisper import _MODELS, _download
                            _download(_MODELS.get(model_key), model_dir, False)
                        else:
                            from faster_whisper.utils import download_model as fw_download_model
                            fw_download_model(model_key, cache_dir=model_dir)
                        result_box["ok"] = True
                    except Exception as exc: result_box["error"] = exc

                dl_thread = Thread(target=_do_download, daemon=True)
                dl_thread.start()

                last_bytes, last_time, start_t = 0, time(), time()
                while dl_thread.is_alive():
                    sleep(0.6)
                    current_bytes, now = self._path_size(observe_path), time()
                    speed_bps = max(0, current_bytes - last_bytes) / max(0.2, now - last_time)
                    speed_text = f"{self._fmt_bytes(speed_bps)}/s" if speed_bps > 0 else "-"
                    progress = min(95.0, max(5.0, (current_bytes / total_bytes * 95.0) if total_bytes > 0 else (5.0 + (now - start_t) * 0.9)))
                    size_text = f"{self._fmt_bytes(current_bytes)}/{self._fmt_bytes(total_bytes)}" if total_bytes > 0 else self._fmt_bytes(current_bytes)

                    self._cache_model_status(engine, model_key, False, downloading=True, progress=progress, speed=speed_text)
                    self.update_task_progress(progress, source="model-download")
                    self.update_task_message(f"DL {model_key}: {size_text} ({speed_text})", source="model-download")
                    last_bytes, last_time = current_bytes, now

                dl_thread.join()
                if result_box.get("error"): raise cast(Exception, result_box["error"])

                self.update_task_progress(90, source="model-download")
                downloaded, error = False, ""
                for _ in range(8):
                    if downloaded := self._verify_model_status(engine, model_key, model_dir)[0]: break
                    sleep(0.5)

                self._cache_model_status(engine, model_key, downloaded, error, downloading=False, progress=100.0 if downloaded else 0.0, speed="-")
                if not downloaded: raise RuntimeError(error or "Verification failed")

                self.update_task_progress(100, source="model-download")
                self.finish_task(f"Model downloaded: {model_key} ({engine})")
            except Exception as exc:
                logger.exception(exc)
                self._cache_model_status(engine, model_key, False, str(exc), downloading=False)
                self.update_task_error(str(exc))
            finally:
                self._model_download_running = False

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "Model download started", "model": model_key, "engine": engine}

    def load_runtime_model(self, model_key: str) -> Dict[str, Any]:
        model_key = self._normalize_model_key(str(model_key))
        if self._model_load_running: return {"ok": False, "message": "Another load is running"}

        self._model_load_running = True
        sj.save_key("model_mw", model_key)
        sj.save_key("model_f_import", model_key)
        self._runtime_model_key = model_key
        self._runtime_model_loaded = False
        self._runtime_model_message = f"Loading model cache for {model_key}"

        def worker():
            try:
                whisper_load_api = _get_whisper_load_api()
                s = cast(SettingDict, self.get_settings_snapshot())
                s["model_mw"] = s["model_f_import"] = model_key
                engine = self._normalize_engine_name(str(s.get("tl_engine_mw", "Google Translate")))
                
                self.reset_task_state("Model Load")
                self.update_task_message(f"Loading model cache for {model_key}")
                self.update_task_progress(5)
                
                whisper_load_api.get_model(bool(s.get("transcribe_mw", True)), bool(s.get("translate_mw", True)), engine in model_values, model_key, engine, s, **whisper_load_api.get_model_args(s))
                
                self.update_task_progress(100)
                self.finish_task(f"Model ready: {model_key}")
                self._runtime_model_loaded = True
                self._runtime_model_message = f"Model ready: {model_key}"
            except Exception as exc:
                logger.exception(exc)
                self.update_task_error(str(exc))
                self._runtime_model_loaded = False
                self._runtime_model_message = f"Model load failed: {exc}"
            finally:
                self._model_load_running = False

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "Model loading started", "model": model_key}

    # =========================================================================
    # SECTION 5: REALTIME RECORDING
    # =========================================================================
    def _wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        """等待实时录音资源完全释放，防止与文件处理发生资源抢占"""
        start_t = time()
        while time() - start_t < timeout_s:
            worker_alive = self._record_worker_thread is not None and self._record_worker_thread.is_alive()
            stream_released = getattr(bc, "stream", None) is None
            rec_flag_off = not getattr(bc, "recording", False)
            if stream_released and rec_flag_off and not worker_alive:
                return True
            sleep(0.05)
        return False
    def update_task_message(self, message: str, source: str = "general"):
        super().update_task_message(message, source=source)
        text = str(message or "").strip()
        if not text: return
        lowered = text.lower()
        with self._lock:
            if lowered.startswith("loading model and preparing pipeline"):
                if not self._runtime_model_loaded:
                    self._model_load_running = True
                    self._runtime_model_message = f"Loading model cache for {self._runtime_model_key}"
                else:
                    self._model_load_running = False
                    self._runtime_model_message = f"Model ready: {self._runtime_model_key}"
                return
            if lowered.startswith("loading model:") or lowered.startswith("loading model cache for"):
                candidate = text.split(":", 1)[1].strip() if ":" in text else ""
                next_key = self._normalize_model_key(candidate) if candidate else self._runtime_model_key
                if self._runtime_model_loaded and next_key and self._runtime_model_key == next_key:
                    self._model_load_running = False
                    self._runtime_model_message = f"Model ready: {self._runtime_model_key}"
                else:
                    self._runtime_model_key = next_key
                    self._model_load_running = True
                    self._runtime_model_loaded = False
                    self._runtime_model_message = f"Loading model cache for {self._runtime_model_key}"
                return
            if lowered.startswith("model loaded:") or lowered.startswith("model ready:"):
                self._runtime_model_key = self._normalize_model_key(text.split(":", 1)[1].strip() if ":" in text else self._runtime_model_key)
                self._model_load_running = False
                self._runtime_model_loaded = True
                self._runtime_model_message = f"Model ready: {self._runtime_model_key}"
                return
            if lowered.startswith("model load failed"):
                self._model_load_running = False
                self._runtime_model_loaded = False
                self._runtime_model_message = text
                return

    def set_recording_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        status_text = str(payload.get("status", "")).lower()
        with self._lock:
            self.recording_state.update(payload)
            if "active" not in payload: self.recording_state["active"] = bool(bc.recording)
            
            if "initializing" in status_text:
                self._model_load_running = True
                self._runtime_model_loaded = False
                if self._runtime_model_key: self._runtime_model_message = f"Loading model cache for {self._runtime_model_key}"
            elif any(x in status_text for x in ["recording", "transcrib", "translat"]):
                self._model_load_running = False
                if self._runtime_model_key:
                    self._runtime_model_loaded = True
                    self._runtime_model_message = f"Model ready: {self._runtime_model_key}"
            elif "stopped" in status_text:
                self._model_load_running = False
                
        self._emit_ui_update(["task"])
        return {"ok": True}

    def get_recording_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.recording_state)

    def start_recording(self, device: str = "mic", lang_source: str = "English", lang_target: str = "Indonesian", engine: str = "Selenium Chrome Translate", is_tc: bool = True, is_tl: bool = True) -> Dict[str, Any]:
        from speech_translate.utils.audio.record import record_session
        from speech_translate.utils.whisper.helper import model_keys

        if bc.recording: return {"ok": False, "message": "Already recording"}

        s = self.get_settings_snapshot()
        lang_source = str(s.get("source_lang_mw", lang_source))
        lang_target = str(s.get("target_lang_mw", lang_target))
        device = str(s.get("input", device))
        engine = self._normalize_engine_name(str(s.get("tl_engine_mw", engine)))
        is_tc = bool(s.get("transcribe_mw", is_tc))
        is_tl = bool(s.get("translate_mw", is_tl))
        model_name_tc = self._normalize_model_key(str(s.get("model_mw", "")))
        self._runtime_model_key = model_name_tc

        if not is_tc and not is_tl: return {"ok": False, "message": "Please enable Transcribe or Translate"}

        cached_bundle = False
        try:
            whisper_load_api = _get_whisper_load_api()
            cached_bundle = whisper_load_api.is_model_bundle_cached(is_tc, is_tl, engine in model_values, model_name_tc, engine, cast(SettingDict, s), **whisper_load_api.get_model_args(cast(SettingDict, s)))
        except Exception: pass

        if cached_bundle:
            self._model_load_running = False
            self._runtime_model_loaded = True
            self._runtime_model_message = f"Model ready: {self._runtime_model_key}"

        self.bind_headless_main_window()
        bc.tc_sentences = []
        bc.tl_sentences = []
        self.clear_live()
        bc.enable_rec()
        
        self.reset_task_state("Recording")
        self.set_recording_state({
            "status": "Preparing recording..." if cached_bundle else "Initializing recording...", "active": True,
            "device": device, "lang_source": lang_source, "lang_target": lang_target, "engine": engine,
            "mode": "Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate",
            "timer": "00:00:00", "buffer": "0/0 sec", "sentences": "0",
        })

        import speech_translate.utils.audio.record as record_module
        record_module.mbox = lambda *args, **kwargs: True

        def worker():
            try:
                record_session(lang_source, lang_target, engine, model_name_tc, device, is_tc, is_tl, device.lower() == "speaker")
                self.finish_task("Recording finished")
            except Exception as exc:
                logger.exception(exc)
                self.update_task_error(str(exc))
            finally:
                bc.disable_rec()
                self.set_recording_state({"status": "Stopped", "active": False})
                if bool(self.get_settings_snapshot().get("selenium_auto_close_on_task_done", True)) and is_tl and engine == "Selenium Chrome Translate":
                    shutdown_selenium_translator()
                self._record_worker_thread = None

        self._record_worker_thread = Thread(target=worker, daemon=True)
        self._record_worker_thread.start()
        return {"ok": True, "device": device, "engine_whisper": engine in model_keys, "message": "Recording started"}

    def stop_recording(self) -> Dict[str, Any]:
        if not bc.recording: return {"ok": False, "message": "Not currently recording"}
        self.set_recording_state({"status": "Stopping...", "active": False})
        bc.disable_rec()
        
        if self._wait_recording_idle(timeout_s=12.0):
            self.set_recording_state({"status": "Stopped", "active": False})
            if bool(self.get_settings_snapshot().get("selenium_auto_close_on_task_done", True)) and self._normalize_engine_name(str(self.get_settings_snapshot().get("tl_engine_mw", ""))) == "Selenium Chrome Translate":
                shutdown_selenium_translator()
            return {"ok": True, "message": "Recording stopped"}
        return {"ok": True, "message": "Stop requested; cleanup is still finishing in background"}

    # =========================================================================
    # SECTION 6: BATCH FILE PROCESSING QUEUE & UI SYNC
    # =========================================================================
    # 补充这个缺失的前端 API 接口
    def get_import_ui_details(self) -> Dict[str, Any]:
        """供前端懒加载请求，扫描磁盘并返回所有可用的模型列表"""    
        return self._build_import_ui(verify_available=True)
    def _build_import_ui(self, verify_available: bool = True) -> Dict[str, Any]:
        s = dict(sj.cache)
        engine = self._normalize_engine_name(str(s.get("tl_engine_f_import", "Selenium Chrome Translate")))
        selected_model_display = str(s.get("model_f_import", "")).strip()
        model_name = self._normalize_model_key(selected_model_display)
        backend = "faster-whisper" if bool(s.get("use_faster_whisper", True)) else "whisper"
        
        available_model_display_names = []
        if verify_available:
            model_dir = self._resolve_model_dir()
            for display_name in list(model_select_dict.keys()):
                if self._is_model_available_for_backend(self._normalize_model_key(display_name), backend, model_dir):
                    available_model_display_names.append(display_name)
            if available_model_display_names:
                if selected_model_display not in available_model_display_names:
                    selected_model_display = available_model_display_names[0]
                model_name = self._normalize_model_key(selected_model_display)
            else:
                selected_model_display = model_name = ""
        else:
            available_model_display_names = [selected_model_display] if selected_model_display else []

        return {
            "backend_options": ["whisper", "faster-whisper"], "selected_backend": backend,
            "model_options": available_model_display_names, "selected_model": selected_model_display, "selected_model_key": model_name,
            "engine_options": ["Selenium Chrome Translate", "Google Translate", "MyMemoryTranslator", "LibreTranslate"] + list(model_select_dict.keys()),
            "selected_engine": engine, "source_options": TL_ENGINE_SOURCE_DICT.get(engine, TL_ENGINE_SOURCE_DICT["Google Translate"]),
            "target_options": TL_ENGINE_TARGET_DICT.get(engine, TL_ENGINE_TARGET_DICT["Google Translate"]),
            "selected_source": s.get("source_lang_f_import"), "selected_target": s.get("target_lang_f_import"),
            "transcribe": s.get("transcribe_f_import"), "translate": s.get("translate_f_import"),
            "queued_files": self._get_full_display_queue(),
        }

    def _get_full_display_queue(self) -> List[Dict[str, Any]]:
        """获取完整的合并队列，用于 UI 渲染，确保已完成的旧文件不会消失"""
        with self._lock:
            display_list = []
            for q in getattr(self, "_file_import_queue", []):
                if isinstance(q, str):
                    display_list.append({"path": q, "name": os.path.basename(q), "status": "", "is_completed": False})
                elif isinstance(q, dict):
                    display_list.append({
                        "path": q.get("path", ""), "name": q.get("name", os.path.basename(q.get("path", ""))),
                        "status": q.get("status", ""), "is_completed": bool(q.get("is_completed", False))
                    })
                else:
                    try: display_list.append({"path": str(q), "name": os.path.basename(str(q)), "status": "", "is_completed": False})
                    except Exception: pass

            if proc_queue := getattr(self, "_processing_queue", []):
                proc_map = {p.get("path"): p for p in proc_queue if p.get("path")}
                for item in display_list:
                    if path := item.get("path"):
                        if path in proc_map:
                            p = proc_map[path]
                            item["status"] = str(p.get("status", item.get("status", "")))
                            item["is_completed"] = bool(p.get("is_completed", item.get("is_completed", False)))
            return display_list

    def get_file_processing_state(self) -> Dict[str, Any]:
        display_queue = self._get_full_display_queue()
        return {
            "ok": True, "files": display_queue, "files_total": len(display_queue),
            "files_completed": sum(1 for item in display_queue if item.get("is_completed", False)),
            "active": bool(getattr(self, "_processing_queue", None)) and bool(getattr(bc, "file_processing", False))
        }
    def init_file_batch(self, task_name: str, files: list):
        """初始化批处理 UI 状态（由 file.py 在任务开始时调用一次）"""
        self.batch_start_time = time()
        with self._lock:
            self.task_state.title = task_name
            # 🛡️ 核心修复：执行队列只装载本次传入的 files_to_process（新文件）
            # 这样 file.py 传过来的 index 才能与这里完美对齐！
            self._processing_queue = []
            for p in files:
                self._processing_queue.append({
                    "path": str(p),
                    "name": os.path.basename(str(p)),
                    "status": "Waiting",
                    "is_completed": False
                })

        # 提取全景队列用于前端 UI 渲染（合并老文件和新文件）
        display_queue = self._get_full_display_queue()
        total = len(display_queue)
        completed_count = sum(1 for item in display_queue if item.get("is_completed", False))
        
        self.update_task_message(f"已准备好 {len(files)} 个待处理文件 | 队列共 {total} 个")
        self.update_task_progress(float((completed_count / total * 100) if total > 0 else 0))
        self.update_task_rows([[item.get('name', ''), item.get('status', '')] for item in display_queue])
        
        def _async_emit():
            try: self._emit_ui_update(["import"])
            except Exception: pass
        Thread(target=_async_emit, daemon=True).start()
    def sync_file_status(self, index: int, combined_status: str, is_completed: bool):
        """同步单个文件的状态，并自动重新计算底部全局进度（由 file.py 频繁调用）"""
        with self._lock:
            # 更新正在执行的批次队列
            if getattr(self, "_processing_queue", []) and 0 <= index < len(self._processing_queue):
                if not self._processing_queue[index].get("is_completed", False) or is_completed:
                    self._processing_queue[index]["status"] = combined_status
                    self._processing_queue[index]["is_completed"] = is_completed

        # UI 更新依然依赖全景视图
        display_queue = self._get_full_display_queue()
        total = len(display_queue)
        completed_count = sum(1 for item in display_queue if item.get("is_completed", False))
        
        elapsed = ""
        if hasattr(self, 'batch_start_time'):
            from time import gmtime, strftime, time
            elapsed = strftime('%H:%M:%S', gmtime(time() - self.batch_start_time))
        
        msg = f"已完成 {completed_count}/{total} 个文件"
        if elapsed:
            msg += f" | 耗时: {elapsed}"

        self.update_task_progress(float((completed_count / total * 100) if total > 0 else 0))
        self.update_task_message(msg)
        self.update_task_rows([[item.get('name', ''), item.get('status', '')] for item in display_queue])
        
        def _async_emit():
            try: self._emit_ui_update(["import"])
            except Exception: pass
        Thread(target=_async_emit, daemon=True).start()
    def add_files_to_import_queue(self, files: Optional[list[str]] = None) -> Dict[str, Any]:
        if not self._wait_recording_idle(timeout_s=12.0): return {"ok": False, "message": "Recording is still cleaning up."}
        if self._model_load_running: return {"ok": False, "message": "Model loading is in progress."}
        if bool(self.get_recording_state().get("active", False)) or bool(bc.recording): return {"ok": False, "message": "Recording is active."}

        if not files:
            if not (window := self.get_window()): return {"ok": False, "message": "Window not ready"}
            webview = import_module("webview")
            files = window.create_file_dialog(getattr(getattr(webview, "FileDialog", object), "OPEN", webview.OPEN_DIALOG), allow_multiple=True, file_types=["Media Files (*.wav;*.mp3;*.ogg;*.flac;*.aac;*.wma;*.m4a;*.mp4;*.mkv;*.avi;*.mov;*.webm)", "All Files (*.*)"])

        if not files: return {"ok": False, "message": "No files selected"}

        added = 0
        with self._lock:
            for f in files:
                if not any((isinstance(q, str) and q == f) or (isinstance(q, dict) and q.get("path") == f) for q in self._file_import_queue):
                    self._file_import_queue.append({"path": f, "name": os.path.basename(f), "status": "Waiting", "is_completed": False})
                    added += 1
        return {"ok": True, "count": len(self._file_import_queue), "added": added, "files": list(self._file_import_queue)}

    def remove_file_from_import_queue(self, index: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            if index is None: return {"ok": False, "message": "缺少索引"}
            try: idx = int(index)
            except Exception: return {"ok": False, "message": "索引无效"}

            if self._processing_queue and 0 <= idx < len(self._processing_queue):
                removed = self._processing_queue.pop(idx)
                path_to_remove = removed.get('path')
                for i, q in enumerate(list(self._file_import_queue)):
                    if (isinstance(q, str) and q == path_to_remove) or (isinstance(q, dict) and q.get('path') == path_to_remove):
                        self._file_import_queue.pop(i)
                        break
            else:
                if idx < 0 or idx >= len(self._file_import_queue): return {"ok": False, "message": "索引超出范围"}
                removed = self._file_import_queue.pop(idx)
                
        try: self._emit_ui_update(["import"])
        except Exception: pass
        return {"ok": True, "files": list(self._file_import_queue), "removed": removed}

    def clear_import_queue(self) -> Dict[str, Any]:
        with self._lock:
            self._file_import_queue = []
            self._processing_queue = []
        try: self._emit_ui_update(["import"])
        except Exception: pass
        return {"ok": True, "files": []}

    def import_files(self, files: Optional[list[str]] = None) -> Dict[str, Any]:
        """Legacy entry: Pick files, add to queue, and start."""
        if not files:
            if not (window := self.get_window()): return {"ok": False, "message": "Window not ready"}
            webview = import_module("webview")
            files = window.create_file_dialog(getattr(getattr(webview, "FileDialog", object), "OPEN", webview.OPEN_DIALOG), allow_multiple=True, file_types=["Media Files (*.wav;*.mp3;*.ogg;*.flac;*.aac;*.wma;*.m4a;*.mp4;*.mkv;*.avi;*.mov;*.webm)", "All Files (*.*)"])
        if not files: return {"ok": False, "message": "No files selected"}
            
        res = self.add_files_to_import_queue(files)
        if not res.get("ok"): return res
        return self.start_import_queue()

    def start_import_queue(self) -> Dict[str, Any]:
        if not self._file_import_queue: return {"ok": False, "message": "No files in queue"}
        if not self._wait_recording_idle(timeout_s=12.0): return {"ok": False, "message": "Recording is still cleaning up."}
        if self._model_load_running: return {"ok": False, "message": "Model loading is still in progress."}

        s = self.get_settings_snapshot()
        from speech_translate.utils.whisper.helper import model_keys
        engine = self._normalize_model_key(str(s.get("tl_engine_f_import", "Google Translate")))
        model_name_tc = self._normalize_model_key(str(s.get("model_f_import", "")))
        is_tc, is_tl = bool(s.get("transcribe_f_import", True)), bool(s.get("translate_f_import", True))

        if is_tc or (is_tl and engine in model_keys):
            if bool(self._runtime_model_loaded) and self._runtime_model_key == model_name_tc:
                self._model_load_running = False
                self._runtime_model_message = f"Model ready: {model_name_tc}"
            else:
                self._runtime_model_key = model_name_tc
                self._runtime_model_loaded = False
                self._model_load_running = True
                self._runtime_model_message = f"Loading model cache for {model_name_tc}"

        # 🛡️ 修复点 2：稳健地提取需要处理的文件路径
        files_to_process = []
        with self._lock:
            for entry in self._file_import_queue:
                if isinstance(entry, dict):
                    if not entry.get("is_completed", False):
                        files_to_process.append(entry.get("path", ""))
                elif isinstance(entry, str):
                    files_to_process.append(entry)

        if not files_to_process: return {"ok": False, "message": "All items are already completed"}

        self.reset_task_state("File Import")
        self.bind_headless_main_window()

        from speech_translate.utils.audio import file as audio_file_module
        audio_file_module.FileProcessDialog = lambda master, title, mode, headers: HeadlessFileProcessDialog(master, title, mode, headers, bridge=self)
        audio_file_module.mbox = headless_mbox

        def worker():
            try:
                bc.enable_file_process()
                audio_file_module.process_file(files_to_process, model_name_tc, str(s.get("source_lang_f_import", "English")), str(s.get("target_lang_f_import", "Indonesian")), is_tc, is_tl, engine)
                summary = ", ".join([f"{bc.file_tced_counter} transcribed"] * is_tc + [f"{bc.file_tled_counter} translated"] * is_tl) or "no output generated"
                self.finish_task(f"File import finished: {summary}")
                if self._model_load_running:
                    self._runtime_model_loaded = True
                    self._runtime_model_message = f"Model ready: {model_name_tc}"
            except Exception as exc:
                logger.exception(exc)
                self.update_task_error(str(exc))
            finally:
                with self._lock:
                    proc_map = {p.get("path"): p for p in getattr(self, "_processing_queue", [])}
                    for i, q in enumerate(self._file_import_queue):
                        path = q if isinstance(q, str) else q.get("path", "")
                        if path in proc_map:
                            proc = proc_map[path]
                            self._file_import_queue[i] = {
                                "path": path, "name": proc.get("name", os.path.basename(path)),
                                "status": proc.get("status", ""), "is_completed": bool(proc.get("is_completed", False))
                            }
                    self._processing_queue = []
                bc.disable_file_process()
                self._model_load_running = False
                try: self._emit_ui_update(["import"])
                except Exception: pass
                if bool(s.get("selenium_auto_close_on_task_done", True)) and is_tl and engine == "Selenium Chrome Translate":
                    shutdown_selenium_translator()

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "count": len(files_to_process), "message": "File import started"}
    def stop_import_queue(self) -> Dict[str, Any]:
        with self._lock:
            if not (bool(getattr(self, "_processing_queue", None)) and len(getattr(self, "_processing_queue", [])) > 0):
                return {"ok": False, "message": "No import is running"}
        bc.disable_file_process()
        with self._lock:
            for item in getattr(self, "_processing_queue", []) or []: item["status"] = "Cancelled"
        try: self.update_task_message("Cancelling file import...", source="import")
        except Exception: pass
        try: self._emit_ui_update(["import"])
        except Exception: pass
        return {"ok": True, "message": "Cancel requested"}

    # =========================================================================
    # SECTION 7: DETACHED WINDOWS
    # =========================================================================

    def get_detached_config(self, mode: str) -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        return {
            "font": sj.cache.get(f"tb_ex_{mode}_font", "Arial"), "font_size": sj.cache.get(f"tb_ex_{mode}_font_size", 13),
            "font_bold": sj.cache.get(f"tb_ex_{mode}_font_bold", True), "font_color": sj.cache.get(f"tb_ex_{mode}_font_color", "#FFFFFF"),
            "bg_color": sj.cache.get(f"tb_ex_{mode}_bg_color", "#000000"), "always_on_top": sj.cache.get(f"ex_{mode}_always_on_top", 0),
            "no_title_bar": sj.cache.get(f"ex_{mode}_no_title_bar", 0), "opacity": sj.cache.get(f"ex_{mode}_opacity", 1.0),
            "click_through": sj.cache.get(f"ex_{mode}_click_through", 0),
        }

    def set_detached_config(self, mode: str, key: str, value: Any) -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        setting_key = f"ex_{mode}_{key}" if key in ("always_on_top", "no_title_bar", "opacity", "click_through") else f"tb_ex_{mode}_{key}"
        sj.save_key(setting_key, value)
        return {"key": setting_key, "value": sj.cache.get(setting_key)}

    def create_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        width, height = _parse_window_size(sj.cache.get(f"ex_{mode}_geometry", "900x240"), 900, 240)
        x, y = _ensure_visible_or_center(*(x, y) if x is not None and y is not None else _center_window_pos(width, height), width, height)
        self.detached_window_manager.create_window(mode, x, y, width, height)
        self.update_detached_config(mode)
        
        live = self.snapshot_live_state()
        if html := live.get(f"detached_{'transcribed' if mode == 'tc' else 'translated'}_html") or live.get(f"detached_{'transcribed' if mode == 'tc' else 'translated'}_text"):
            self.update_detached_content(mode, str(html))
        return {"status": "created", "mode": mode}

    def toggle_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        if mode in self.detached_window_manager.windows:
            self.detached_window_manager.close_window(mode)
            return {"status": "closed", "mode": mode}
        return self.create_detached_window(mode, x, y)

    def show_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        self.detached_window_manager.show_window(mode)
        return {"status": "shown", "mode": mode}

    def hide_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        self.detached_window_manager.hide_window(mode)
        return {"status": "hidden", "mode": mode}

    def close_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        self.detached_window_manager.close_window(mode)
        return {"status": "closed", "mode": mode}

    def update_detached_content(self, mode: str, html_content: str) -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        if mode not in self.detached_window_manager.windows: return {"status": "missing", "mode": mode}
        self.detached_window_manager.update_window_content(mode, html_content)
        return {"status": "updated", "mode": mode}

    def update_detached_config(self, mode: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        mode = str(mode).lower() if str(mode).lower() in {"tc", "tl"} else "tl"
        self.detached_window_manager.update_window_config(mode, config or self.get_detached_config(mode))
        return {"status": "config_updated", "mode": mode}

class AppTray:

    """System tray integration for the webview app."""

    def __init__(self, bridge: WebBridge):
        self.bridge = bridge
        self.icon = None
        self._create_tray()

    def _fallback_image(self, width: int, height: int, color1: str, color2: str):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (width, height), color1)  # type: ignore[arg-type]
        drawer = ImageDraw.Draw(image)
        drawer.rectangle((width // 2, 0, width, height // 2), fill=color2)
        drawer.rectangle((0, height // 2, width // 2, height), fill=color2)
        return image

    def _create_tray(self):
        import pystray
        from PIL import Image

        try:
            ico = Image.open(p_app_icon)
        except Exception:
            ico = self._fallback_image(64, 64, "black", "white")

        self.icon = pystray.Icon(
            "Speech Translate",
            ico,
            f"Speech Translate V{__version__}",
            menu=pystray.Menu(
                pystray.MenuItem("Show App", self.show_app),
                pystray.MenuItem("Open Export Folder", lambda *_: self.bridge.open_directory("export")),
                pystray.MenuItem("Open Log Folder", lambda *_: self.bridge.open_directory("log")),
                pystray.MenuItem("Open Debug Folder", lambda *_: self.bridge.open_directory("debug")),
                pystray.MenuItem("Open Model Folder", lambda *_: self.bridge.open_directory("model")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Visit Repository", lambda *_: open_url("https://github.com/Dadangdut33/Speech-Translate")),
                pystray.MenuItem("Exit", self.exit_app),
            ),
        )
        self.icon.run_detached()

    def show_app(self, *_args):
        window = self.bridge.get_window()
        if window is not None:
            try:
                window.show()
            except Exception:
                pass
            try:
                window.bring_to_front()
            except Exception:
                pass

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def exit_app(self, *_args):
        self.bridge.quit_app()


def _install_signal_handler():
    def signal_handler(_sig, _frame):
        logger.info("Received Ctrl+C, exiting...")
        bridge = getattr(bc, "web_bridge", None)
        if bridge is not None:
            bridge.quit_app()

    signal(SIGINT, signal_handler)


def _build_html_path() -> str:
    return str(Path(__file__).with_name("web") / "index.html")


def main(with_log_init: bool = True):
    startup_t0 = time()
    if with_log_init:
        init_logging(sj.cache["log_level"])

    logger.info(f"App Version: {__version__} - TIME: {strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"OS: {system()} {release()} {version()} | CPU: {processor()}")
    logger.debug(f"Sys args: {sys.argv}")
    logger.debug("Loading Web UI...")

    _install_signal_handler()
    logger.debug("[Startup] before_add_ffmpeg")
    add_ffmpeg_to_path(weak=True)
    logger.debug("[Startup] after_add_ffmpeg")
    logger.debug("[Startup] before_import_webview")
    webview = import_module("webview")
    logger.debug("[Startup] after_import_webview")

    logger.debug("[Startup] before_bridge_init")
    bridge = WebBridge()
    logger.debug("[Startup] after_bridge_init")
    bridge.set_startup_t0(startup_t0)
    setattr(bc, "web_bridge", bridge)

    tray_enabled = "--no-tray" not in sys.argv

    raw_main_size = str(sj.cache.get("mw_size", "980x620") or "980x620").strip()
    if raw_main_size == "1140x680":
        # One-time migration from legacy default to the new smaller default.
        sj.save_key("mw_size", "980x620")
        raw_main_size = "980x620"

    main_width, main_height = _parse_window_size(raw_main_size, 980, 620)
    main_x, main_y = _center_window_pos(main_width, main_height)
    main_x, main_y = _ensure_visible_or_center(main_x, main_y, main_width, main_height)

    bridge._log_startup_marker("before_create_main_window")
    window = webview.create_window(
        APP_NAME,
        _build_html_path(),
        js_api=bridge,
        width=main_width,
        height=main_height,
        x=main_x,
        y=main_y,
        min_size=(880, 560),
        hidden=True,
    )
    bridge._log_startup_marker("after_create_main_window")
    bridge.bind_window(window)

    debug_enabled = "--debug-webview" in sys.argv or "--debug" in sys.argv
    bridge._log_startup_marker("before_webview_start")

    def _on_webview_ready():
        bridge._log_startup_marker("webview_ready_callback")
        if tray_enabled and bridge.get_tray() is None:
            try:
                bridge._log_startup_marker("before_tray_init")
                tray = AppTray(bridge)
                bridge.bind_tray(tray)
                bridge._log_startup_marker("after_tray_init")
            except Exception as exc:
                logger.exception(exc)

    webview.start(_on_webview_ready, debug=debug_enabled)
