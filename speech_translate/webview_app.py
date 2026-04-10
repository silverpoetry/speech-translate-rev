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
from typing import Any, Dict, Optional, cast
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
        self._last_content_payload = {}  # {mode: html_content}
        self._last_config_payload = {}  # {mode: config_json}
        self._window_style_cache = {}  # {mode: (style, ex_style)}
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

    def _flush_pending(self, mode: str) -> None:
        if not self._window_loaded.get(mode):
            return
        if mode in self.pending_configs:
            self.update_window_config(mode, self.pending_configs[mode])
        if mode in self.pending_updates:
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
        if mode not in self.windows:
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

    def _drop_window_ref(self, mode: str):
        if mode in self.windows:
            self.windows.pop(mode, None)
            self._window_loaded.pop(mode, None)
            self._last_content_payload.pop(mode, None)
            self._last_config_payload.pop(mode, None)
            logger.debug(f"Dropped detached window reference: {mode}")

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
            self._attach_window_events(mode, window)

            # If the user closes the window from the title bar, clear cached reference
            # so opening again creates a new instance.
            logger.info(f"Created detached window: {mode}")

            self._flush_pending(mode)

            # Enforce topmost immediately in case runtime backend ignores create args.
            self._apply_topmost(mode, focus_nudge=False)
            self._apply_native_window_settings(mode)

        except Exception as e:
            logger.error(f"Failed to create detached window: {e}")

        return self.windows.get(mode)

    def _on_window_loaded(self, mode: str) -> None:
        self._window_loaded[mode] = True

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

        def _flush_async():
            try:
                sleep(0.05)
                self._flush_pending(mode)
            except Exception:
                pass

        Thread(target=_flush_async, daemon=True).start()

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
        self.pending_updates[mode] = html_content
        if mode in self.windows and self._window_loaded.get(mode):
            if self._last_content_payload.get(mode) == html_content:
                return
            try:
                self.windows[mode].evaluate_js(
                    f"window.postMessage({{type: 'update-content', html: {repr(html_content)}}}, '*')"
                )
                self._last_content_payload[mode] = html_content
                logger.debug(f"Updated content for window: {mode}")
            except Exception as e:
                logger.error(f"Failed to update window content: {e}")
                self._drop_window_ref(mode)

    def update_window_config(self, mode: str, config: Dict[str, Any]):
        """Send configuration to detached window."""
        self.pending_configs[mode] = config
        self._apply_native_window_settings(mode, config)
        if mode in self.windows and self._window_loaded.get(mode):
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
                self._apply_topmost(mode, focus_nudge=False)
            except Exception as e:
                logger.error(f"Failed to update window config: {e}")


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
    """Bridge exposed to the pywebview frontend."""

    def __init__(self):
        super().__init__()
        self._startup_t0: Optional[float] = None
        self._first_state_logged = False
        self._main_window_show_allowed = False
        self._main_geometry_lock = Lock()
        self._main_geometry_last_saved = ""
        self.detached_window_manager = DetachedWindowManager(self)
        self._model_status_cache: Dict[str, Dict[str, Any]] = {}
        self._model_download_running = False
        self._model_load_running = False
        self._runtime_model_key = self._normalize_model_key(str(sj.cache.get("model_f_import", "")))
        self._runtime_model_loaded = False
        self._runtime_model_message = "模型未预加载"
        self._model_manager_engine = "whisper"
        self._model_manager_model = "small"
        self._audio_source_cache: Dict[str, Any] = {
            "host_api_options": [],
            "mic_options_by_host": {},
            "speaker_options_by_host": {},
            "mic_options_all": [],
            "speaker_options_all": [],
        }
        self._audio_source_cache_ready = False
        self._audio_source_cache_loading = True
        Thread(target=self._prime_audio_source_cache, daemon=True).start()
        self.recording_state: Dict[str, Any] = {
            "status": "Idle",
            "active": False,
            "device": "-",
            "lang_source": "-",
            "lang_target": "-",
            "engine": "-",
            "mode": "-",
            "timer": "00:00:00",
            "buffer": "0/0 sec",
            "sentences": "0",
        }
        self._record_worker_thread: Optional[Thread] = None

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

    def _wait_recording_idle(self, timeout_s: float = 12.0) -> bool:
        """Wait until realtime recording resources are fully released."""
        start_t = time()
        while time() - start_t < timeout_s:
            worker_alive = self._record_worker_thread is not None and self._record_worker_thread.is_alive()
            stream_released = bc.stream is None
            rec_flag_off = not bc.recording
            if stream_released and rec_flag_off and not worker_alive:
                return True
            sleep(0.05)
        return False

    def update_task_message(self, message: str, source: str = "general"):
        super().update_task_message(message, source=source)

        text = str(message or "").strip()
        if not text:
            return

        lowered = text.lower()
        with self._lock:
            if lowered.startswith("loading model and preparing pipeline"):
                # Do not downgrade a warm model to loading when import starts.
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
                    # Same model already resident: keep loaded state instead of flickering to loading.
                    self._model_load_running = False
                    self._runtime_model_message = f"Model ready: {self._runtime_model_key}"
                else:
                    self._runtime_model_key = next_key
                    self._model_load_running = True
                    self._runtime_model_loaded = False
                    self._runtime_model_message = f"Loading model cache for {self._runtime_model_key}"
                return

            if lowered.startswith("model loaded:") or lowered.startswith("model ready:"):
                # As soon as model init is done, show loaded even while file processing continues.
                model_key = text.split(":", 1)[1].strip() if ":" in text else self._runtime_model_key
                if model_key:
                    self._runtime_model_key = self._normalize_model_key(model_key)
                self._model_load_running = False
                self._runtime_model_loaded = True
                self._runtime_model_message = f"Model ready: {self._runtime_model_key}"
                return

            if lowered.startswith("model load failed"):
                self._model_load_running = False
                self._runtime_model_loaded = False
                self._runtime_model_message = text
                return

    def _prime_audio_source_cache(self) -> None:
        try:
            host_api_options = get_host_apis()
            mic_options_all = get_input_devices("")
            speaker_options_all = get_output_devices("")

            default_host_api = ""
            default_mic = ""
            default_speaker = ""

            ok_default_host, default_host_info = get_default_host_api()
            if ok_default_host and isinstance(default_host_info, dict):
                default_host_api = str(default_host_info.get("name", ""))

            ok_default_mic, default_mic_info = get_default_input_device()
            if ok_default_mic and isinstance(default_mic_info, dict):
                default_mic_name = str(default_mic_info.get("name", ""))
                if default_mic_name:
                    default_mic = next(
                        (
                            str(item)
                            for item in mic_options_all
                            if isinstance(item, str)
                            and "[ID:" in item
                            and default_mic_name.lower() in item.lower()
                        ),
                        "",
                    )

            ok_default_speaker, default_speaker_info = get_default_output_device()
            if ok_default_speaker and isinstance(default_speaker_info, dict):
                default_speaker_name = str(default_speaker_info.get("name", ""))
                if default_speaker_name:
                    default_speaker = next(
                        (
                            str(item)
                            for item in speaker_options_all
                            if isinstance(item, str)
                            and "[ID:" in item
                            and default_speaker_name.lower() in item.lower()
                        ),
                        "",
                    )

            mic_options_by_host: Dict[str, Any] = {}
            speaker_options_by_host: Dict[str, Any] = {}
            for host_api in host_api_options:
                if isinstance(host_api, str) and host_api.startswith("["):
                    continue
                mic_options_by_host[host_api] = get_input_devices(str(host_api))
                speaker_options_by_host[host_api] = get_output_devices(str(host_api))

            self._audio_source_cache = {
                "host_api_options": host_api_options,
                "mic_options_by_host": mic_options_by_host,
                "speaker_options_by_host": speaker_options_by_host,
                "mic_options_all": mic_options_all,
                "speaker_options_all": speaker_options_all,
                "default_host_api": default_host_api,
                "default_mic": default_mic,
                "default_speaker": default_speaker,
            }
        except Exception as exc:
            logger.exception(exc)
            self._audio_source_cache = {
                "host_api_options": [],
                "mic_options_by_host": {},
                "speaker_options_by_host": {},
                "mic_options_all": ["[ERROR] Failed to load input devices"],
                "speaker_options_all": ["[ERROR] Failed to load output devices"],
                "default_host_api": "",
                "default_mic": "",
                "default_speaker": "",
            }
        finally:
            self._audio_source_cache_loading = False
            self._audio_source_cache_ready = True
            try:
                self._emit_ui_update(["state"])
            except Exception:
                pass

    def bind_window(self, window):
        super().bind_window(window)
        self._log_startup_marker("bind_window")
        try:
            if hasattr(window, "events") and hasattr(window.events, "shown"):
                window.events.shown += lambda *_: self._on_main_window_shown(window)
            if hasattr(window, "events") and hasattr(window.events, "loaded"):
                window.events.loaded += lambda *_: self._log_startup_marker("main_window_loaded")
            if hasattr(window, "events") and hasattr(window.events, "closed"):
                window.events.closed += lambda *_: self._save_main_window_geometry(force=True)
        except Exception:
            pass

    def _on_main_window_shown(self, window) -> None:
        if not self._main_window_show_allowed:
            try:
                window.hide()
            except Exception:
                pass
        self._log_startup_marker("main_window_shown")

    def show_main_window(self) -> None:
        self._main_window_show_allowed = True
        window = self.get_window()
        if window is None:
            return

        try:
            window.show()
        except Exception:
            return

        try:
            window.bring_to_front()
        except Exception:
            pass

        self._log_startup_marker("main_window_shown_after_init")

    def _save_main_window_geometry(self, force: bool = False) -> None:
        window = self.get_window()
        if window is None:
            return

        native_window = getattr(window, "native", None)
        if native_window is None:
            return

        width = None
        height = None
        raw_width = None
        raw_height = None
        scale_factor = 1.0

        try:
            scale_factor = float(getattr(native_window, "scale_factor", 1.0) or 1.0)
            if scale_factor <= 0:
                scale_factor = 1.0
        except Exception:
            scale_factor = 1.0

        try:
            client_size = getattr(native_window, "ClientSize", None)
            if client_size is not None:
                raw_width = int(getattr(client_size, "Width"))
                raw_height = int(getattr(client_size, "Height"))
                width = int(round(raw_width / scale_factor))
                height = int(round(raw_height / scale_factor))
        except Exception:
            width = None
            height = None

        if width is None or height is None:
            try:
                width = int(getattr(window, "width"))
                height = int(getattr(window, "height"))
            except Exception:
                return

        if width >= 600 and height >= 300:
            geometry = f"{width}x{height}"
            with self._main_geometry_lock:
                if not force and geometry == self._main_geometry_last_saved:
                    return

                self._main_geometry_last_saved = geometry
                sj.save_key("mw_size", geometry)

            logger.info(
                f"[MainGeometry][save] logical={geometry} "
                f"raw_client={raw_width}x{raw_height} scale_factor={scale_factor:.3f} force={force}"
            )

    def bind_tray(self, tray):
        super().bind_tray(tray)

    def _resolve_model_dir(self) -> str:
        configured = sj.cache.get("dir_model", "auto")
        return configured if configured != "auto" else get_default_download_root()

    def _get_model_manager_keys(self) -> list[str]:
        base_models = ["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3"]
        result: list[str] = []
        for model_key in base_models:
            result.append(model_key)
            if "large" not in model_key:
                result.append(f"{model_key}.en")
        return result

    def _verify_model_status(self, engine: str, model_key: str, model_dir: str) -> tuple[bool, str]:
        try:
            if engine == "faster-whisper":
                return verify_model_faster_whisper(model_key, model_dir), ""
            return verify_model_whisper(model_key, model_dir), ""
        except Exception as exc:
            logger.exception(exc)
            return False, str(exc)

    def _cache_model_status(
        self,
        engine: str,
        model_key: str,
        downloaded: bool,
        error: str = "",
        downloading: bool = False,
        progress: Optional[float] = None,
        speed: str = "",
    ) -> None:
        cache_key = f"{engine}:{model_key}"
        if progress is None:
            progress = 100.0 if downloaded else 0.0
        self._model_status_cache[cache_key] = {
            "engine": engine,
            "model": model_key,
            "downloaded": downloaded,
            "error": error,
            "downloading": downloading,
            "progress": float(max(0.0, min(100.0, progress))),
            "speed": speed,
        }

    @staticmethod
    def _path_size(path: str) -> int:
        if not path:
            return 0
        if os.path.isfile(path):
            try:
                return os.path.getsize(path)
            except Exception:
                return 0
        if os.path.isdir(path):
            total = 0
            for root, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except Exception:
                        pass
            return total
        return 0

    @staticmethod
    def _fmt_bytes(value: float) -> str:
        if value <= 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        idx = 0
        while size >= 1024.0 and idx < len(units) - 1:
            size /= 1024.0
            idx += 1
        if idx == 0:
            return f"{int(size)} {units[idx]}"
        return f"{size:.1f} {units[idx]}"

    def _estimate_total_whisper_bytes(self, model_key: str) -> int:
        try:
            from whisper import _MODELS  # pylint: disable=import-outside-toplevel

            url = _MODELS.get(model_key)
            if not url:
                return 0
            req = Request(url, method="HEAD")
            with urlopen(req, timeout=6) as resp:  # nosec B310
                content_length = resp.headers.get("Content-Length")
            return int(content_length) if content_length else 0
        except Exception:
            return 0

    def _build_model_manager_state(self, engine_hint: Optional[str] = None, include_both: bool = False) -> Dict[str, Any]:
        model_dir = self._resolve_model_dir()
        engines = ["whisper", "faster-whisper"]
        selected_engine = str(engine_hint or self._model_manager_engine or "whisper")
        if selected_engine not in engines:
            selected_engine = "whisper"
        self._model_manager_engine = selected_engine

        models = self._get_model_manager_keys()
        selected_model = str(self._model_manager_model or "small")
        if selected_model not in models:
            selected_model = "small"
        self._model_manager_model = selected_model

        rows = []
        row_engines = engines if include_both else [selected_engine]
        for row_engine in row_engines:
            for model_key in models:
                cache_key = f"{row_engine}:{model_key}"
                cached = self._model_status_cache.get(cache_key)
                rows.append(
                    {
                        "model": model_key,
                        "engine": row_engine,
                        "downloaded": cached.get("downloaded") if cached else None,
                        "downloading": cached.get("downloading") if cached else False,
                        "progress": float(cached.get("progress", 0.0)) if cached else 0.0,
                        "speed": str(cached.get("speed", "")) if cached else "",
                        "error": cached.get("error") if cached else "",
                    }
                )

        return {
            "engine_options": engines,
            "model_options": models,
            "selected_engine": selected_engine,
            "selected_model": selected_model,
            "model_dir": model_dir,
            "download_running": self._model_download_running,
            "view_scope": "both" if include_both else "selected",
            "rows": rows,
        }

    def _normalize_model_key(self, value: str) -> str:
        if value in model_select_dict:
            return model_select_dict[value]
        if value in model_values:
            return value
        for display_name, model_key in model_select_dict.items():
            if model_key == value:
                return model_key
        return value

    def _normalize_engine_name(self, value: str) -> str:
        if value in TL_ENGINE_SOURCE_DICT:
            return value
        return value

    def _is_model_available_for_backend(self, model_key: str, backend: str, model_dir: str) -> bool:
        if backend == "faster-whisper":
            try:
                return verify_model_faster_whisper(model_key, model_dir)
            except Exception:
                return False
        return os.path.exists(os.path.join(model_dir, f"{model_key}.pt"))

    def _build_import_ui(self, verify_available: bool = True) -> Dict[str, Any]:
        settings = dict(sj.cache)
        engine = self._normalize_engine_name(str(settings.get("tl_engine_f_import", "Selenium Chrome Translate")))
        selected_model_display = str(settings.get("model_f_import", "")).strip()
        model_name = self._normalize_model_key(selected_model_display)
        backend = "faster-whisper" if bool(settings.get("use_faster_whisper", True)) else "whisper"
        model_dir = self._resolve_model_dir()
        downloadable_model_keys = list(model_select_dict.keys())
        if verify_available:
            available_model_display_names = []
            for display_name in downloadable_model_keys:
                key = self._normalize_model_key(display_name)
                if self._is_model_available_for_backend(key, backend, model_dir):
                    available_model_display_names.append(display_name)

            if available_model_display_names:
                if selected_model_display not in available_model_display_names:
                    selected_model_display = available_model_display_names[0]
                    model_name = self._normalize_model_key(selected_model_display)
                else:
                    model_name = self._normalize_model_key(selected_model_display)
            else:
                selected_model_display = ""
                model_name = ""
        else:
            # Fast-start path: defer expensive model availability checks.
            available_model_display_names = [selected_model_display] if selected_model_display else []

        source_options = TL_ENGINE_SOURCE_DICT.get(engine, TL_ENGINE_SOURCE_DICT["Google Translate"])
        target_options = TL_ENGINE_TARGET_DICT.get(engine, TL_ENGINE_TARGET_DICT["Google Translate"])
        return {
            "backend_options": ["whisper", "faster-whisper"],
            "selected_backend": backend,
            "model_options": available_model_display_names,
            "engine_options": [
                "Selenium Chrome Translate",
                "Google Translate",
                "MyMemoryTranslator",
                "LibreTranslate",
            ] + [x for x in model_select_dict.keys()],
            "source_options": source_options,
            "target_options": target_options,
            "selected_model": selected_model_display,
            "selected_model_key": model_name,
            "selected_engine": engine,
            "selected_source": settings.get("source_lang_f_import"),
            "selected_target": settings.get("target_lang_f_import"),
            "transcribe": settings.get("transcribe_f_import"),
            "translate": settings.get("translate_f_import"),
        }

    def get_import_ui_details(self) -> Dict[str, Any]:
        return self._build_import_ui(verify_available=True)

    def _build_main_ui(self) -> Dict[str, Any]:
        settings = dict(sj.cache)
        return {
            "input_options": ["mic", "speaker"],
            "source_options": WHISPER_LANG_LIST,
            "target_options": WHISPER_LANG_LIST,
            "engine_options": ["Selenium Chrome Translate", "Google Translate", "MyMemoryTranslator", "LibreTranslate"],
            "selected_input": settings.get("input"),
            "selected_source": settings.get("source_lang_mw"),
            "selected_target": settings.get("target_lang_mw"),
            "selected_engine": settings.get("tl_engine_mw"),
            "transcribe": settings.get("transcribe_mw", True),
            "translate": settings.get("translate_mw", True),
            "auto_scroll_log": settings.get("auto_scroll_log"),
            "auto_refresh_log": settings.get("auto_refresh_log"),
        }

    def _build_record_device_ui(self, device: str) -> Dict[str, Any]:
        settings = dict(sj.cache)
        return {
            "sample_rate": settings.get(f"sample_rate_{device}"),
            "chunk_size": settings.get(f"chunk_size_{device}"),
            "channels": settings.get(f"channels_{device}"),
            "auto_sample_rate": settings.get(f"auto_sample_rate_{device}"),
            "auto_channels": settings.get(f"auto_channels_{device}"),
            "min_input": settings.get(f"min_input_length_{device}"),
            "max_buffer": settings.get(f"max_buffer_{device}"),
            "max_sentences": settings.get(f"max_sentences_{device}"),
            "no_limit": settings.get(f"{device}_no_limit"),
            "threshold_enable": settings.get(f"threshold_enable_{device}"),
            "threshold_auto": settings.get(f"threshold_auto_{device}"),
            "auto_break_buffer": settings.get(f"auto_break_buffer_{device}"),
            "threshold_auto_level": settings.get(f"threshold_auto_level_{device}"),
            "threshold_auto_silero": settings.get(f"threshold_auto_silero_{device}"),
            "threshold_silero_min": settings.get(f"threshold_silero_{device}_min"),
            "threshold_db": settings.get(f"threshold_db_{device}"),
        }

    def _build_audio_source_options(self, selected_host_api: Optional[str] = None) -> Dict[str, Any]:
        settings = dict(sj.cache)
        host_api = str(settings.get("hostAPI", "") if selected_host_api is None else selected_host_api)

        host_api_options = self._audio_source_cache.get("host_api_options", [])
        default_host_api = str(self._audio_source_cache.get("default_host_api", ""))
        if not host_api_options:
            fallback_host_api = str(settings.get("hostAPI", "") or host_api or "")
            host_api_options = [fallback_host_api] if fallback_host_api else []
        if host_api and host_api not in host_api_options:
            host_api = ""
        if not host_api and default_host_api in host_api_options:
            host_api = default_host_api
        if not host_api:
            host_api = str(next((x for x in host_api_options if isinstance(x, str) and not x.startswith("[")), ""))

        if host_api:
            mic_options = self._audio_source_cache.get("mic_options_by_host", {}).get(host_api) or []
            speaker_options = self._audio_source_cache.get("speaker_options_by_host", {}).get(host_api) or []
        else:
            mic_options = self._audio_source_cache.get("mic_options_all", [])
            speaker_options = self._audio_source_cache.get("speaker_options_all", [])
        if not mic_options:
            fallback_mic = str(settings.get("mic", "") or "")
            mic_options = [fallback_mic] if fallback_mic else []
        if not speaker_options:
            fallback_speaker = str(settings.get("speaker", "") or "")
            speaker_options = [fallback_speaker] if fallback_speaker else []

        selected_mic = settings.get("mic")
        selected_speaker = settings.get("speaker")
        default_mic = self._audio_source_cache.get("default_mic", "")
        default_speaker = self._audio_source_cache.get("default_speaker", "")

        if selected_mic not in mic_options:
            selected_mic = default_mic if default_mic in mic_options else (mic_options[0] if mic_options else "")
        if selected_speaker not in speaker_options:
            selected_speaker = default_speaker if default_speaker in speaker_options else (
                speaker_options[0] if speaker_options else ""
            )

        return {
            "host_api_options": host_api_options,
            "mic_options": mic_options,
            "speaker_options": speaker_options,
            "selected_host_api": host_api,
            "selected_mic": selected_mic,
            "selected_speaker": selected_speaker,
        }

    def _build_record_ui(self) -> Dict[str, Any]:
        settings = dict(sj.cache)
        audio_sources = self._build_audio_source_options()
        return {
            "input": settings.get("input"),
            "host_api": settings.get("hostAPI"),
            "mic": settings.get("mic"),
            "speaker": settings.get("speaker"),
            "host_api_options": audio_sources.get("host_api_options", []),
            "mic_options": audio_sources.get("mic_options", []),
            "speaker_options": audio_sources.get("speaker_options", []),
            "verbose_record": settings.get("verbose_record"),
            "transcribe_rate": settings.get("transcribe_rate"),
            "separate_with": settings.get("separate_with"),
            "use_temp": settings.get("use_temp"),
            "keep_temp": settings.get("keep_temp"),
            "file_use_official_whisper": settings.get("file_use_official_whisper", False),
            "show_audio_visualizer_in_setting": settings.get("show_audio_visualizer_in_setting"),
            "mic_device": self._build_record_device_ui("mic"),
            "speaker_device": self._build_record_device_ui("speaker"),
        }

    def _build_runtime_model_state(self) -> Dict[str, Any]:
        loaded = bool(self._runtime_model_loaded)
        loading = bool(self._model_load_running) and not loaded
        return {
            "key": self._runtime_model_key,
            "loading": loading,
            "loaded": loaded,
            "message": self._runtime_model_message,
        }

    def _build_about(self) -> Dict[str, Any]:
        return {
            "name": APP_NAME,
            "version": __version__,
            "os": f"{system()} {release()} {version()}",
            "cpu": processor(),
            "log_file": self.get_log_file_name(),
            "model_dir": self._resolve_model_dir(),
            "export_dir": self._resolve_export_dir(),
        }

    def _resolve_export_dir(self) -> str:
        configured = sj.cache.get("dir_export", "auto")
        return configured if configured != "auto" else dir_export

    def _resolve_log_dir(self) -> str:
        configured = sj.cache.get("dir_log", "auto")
        return configured if configured != "auto" else dir_log

    def _resolve_selenium_chrome_user_data_dir(self) -> str:
        configured = str(sj.cache.get("selenium_chrome_user_data_dir", "") or "").strip()
        if configured:
            return configured
        return str(Path(dir_user) / "selenium_chrome_profile")

    def get_state(self) -> Dict[str, Any]:
        state_t0 = time()
        settings = dict(sj.cache)
        t_settings = time()
        compact_settings = {
            "theme": settings.get("theme"),
            "log_level": settings.get("log_level"),
            "dir_export": settings.get("dir_export"),
            "dir_model": settings.get("dir_model"),
            "export_to": settings.get("export_to"),
            "source_lang_mw": settings.get("source_lang_mw"),
            "target_lang_mw": settings.get("target_lang_mw"),
            "input": settings.get("input"),
            "tl_engine_mw": settings.get("tl_engine_mw"),
            "transcribe_mw": settings.get("transcribe_mw", True),
            "translate_mw": settings.get("translate_mw", True),
            "auto_scroll_log": settings.get("auto_scroll_log"),
            "auto_refresh_log": settings.get("auto_refresh_log"),
            "source_lang_f_import": settings.get("source_lang_f_import"),
            "target_lang_f_import": settings.get("target_lang_f_import"),
            "transcribe_f_import": settings.get("transcribe_f_import"),
            "translate_f_import": settings.get("translate_f_import"),
            "tl_engine_f_import": settings.get("tl_engine_f_import"),
            "model_f_import": settings.get("model_f_import"),
            "selenium_compact_level": settings.get("selenium_compact_level", 2),
            "selenium_z_order_mode": settings.get("selenium_z_order_mode", "behind-main"),
            "selenium_auto_close_on_task_done": settings.get("selenium_auto_close_on_task_done", True),
            "selenium_chrome_user_data_dir": settings.get("selenium_chrome_user_data_dir", ""),
        }

        import_ui = self._build_import_ui(verify_available=False)
        t_import = time()
        main_ui = self._build_main_ui()
        t_main = time()
        record_ui = self._build_record_ui()
        t_record = time()
        runtime_model = self._build_runtime_model_state()
        t_runtime = time()
        live_ui = self.snapshot_live_state()
        t_live = time()
        about = self._build_about()
        t_about = time()
        current_log = self.get_log_file_name()
        log_content = self.get_log_content()
        t_log = time()

        result = asdict(
            AppState(
                app_name=APP_NAME,
                version=__version__,
                os_name=system(),
                os_release=release(),
                os_version=version(),
                cpu=processor(),
                settings=compact_settings,
                import_ui=import_ui,
                main_ui=main_ui,
                record_ui=record_ui,
                runtime_model=runtime_model,
                live_ui=live_ui,
                about=about,
                log_level=sj.cache.get("log_level", "DEBUG"),
                current_log=current_log,
                log_content=log_content,
            )
        )
        result["detached_config"] = {
            "tc": self.get_detached_config("tc"),
            "tl": self.get_detached_config("tl"),
        }

        if not self._first_state_logged:
            self._first_state_logged = True
            self._log_startup_marker("first_get_state")
            logger.debug(
                "[StartupState] get_state breakdown ms: "
                f"settings={int((t_settings - state_t0) * 1000)} "
                f"import_ui={int((t_import - t_settings) * 1000)} "
                f"main_ui={int((t_main - t_import) * 1000)} "
                f"record_ui={int((t_record - t_main) * 1000)} "
                f"runtime_model={int((t_runtime - t_record) * 1000)} "
                f"live_ui={int((t_live - t_runtime) * 1000)} "
                f"about={int((t_about - t_live) * 1000)} "
                f"log={int((t_log - t_about) * 1000)} "
                f"total={int((t_log - state_t0) * 1000)}"
            )
        return result

    def get_log_file_name(self) -> str:
        from speech_translate._logging import current_log

        return current_log

    def get_log_content(self) -> str:
        from speech_translate._logging import current_log

        log_path = Path(dir_log) / current_log
        try:
            content = log_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"Log file not found: {log_path}"
        except Exception as exc:
            logger.exception(exc)
            return f"Failed to read log file: {exc}"

        if len(content) > 200000:
            content = content[-200000:]
        return content

    def refresh_log(self) -> Dict[str, str]:
        return {"content": self.get_log_content(), "file": self.get_log_file_name()}

    def clear_log(self) -> Dict[str, str]:
        from speech_translate._logging import clear_current_log_file

        clear_current_log_file()
        logger.info("Log cleared from web UI")
        return self.refresh_log()

    def get_setting(self, key: str) -> Any:
        return sj.cache.get(key)

    def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key == "selenium_settings":
            payload = value if isinstance(value, dict) else {}

            try:
                compact = int(payload.get("compact_level", 2))
            except Exception:
                compact = 2
            compact = max(0, min(3, compact))

            z_order_raw = str(payload.get("z_order_mode", "behind-main")).strip().lower()
            allowed_z = {"normal", "behind-main", "bottom"}
            z_order = z_order_raw if z_order_raw in allowed_z else "behind-main"

            auto_close = bool(payload.get("auto_close_on_task_done", True))
            chrome_user_data_dir = str(payload.get("chrome_user_data_dir", "") or "").strip()

            # Keep Selenium settings update atomic under a single API request.
            sj.save_key("selenium_compact_level", compact)
            sj.save_key("selenium_z_order_mode", z_order)
            sj.save_key("selenium_auto_close_on_task_done", auto_close)
            sj.save_key("selenium_chrome_user_data_dir", chrome_user_data_dir)

            return {
                "key": key,
                "value": {
                    "selenium_compact_level": sj.cache.get("selenium_compact_level", compact),
                    "selenium_z_order_mode": sj.cache.get("selenium_z_order_mode", z_order),
                    "selenium_auto_close_on_task_done": sj.cache.get("selenium_auto_close_on_task_done", auto_close),
                    "selenium_chrome_user_data_dir": sj.cache.get("selenium_chrome_user_data_dir", chrome_user_data_dir),
                },
            }

        if key == "selenium_compact_level":
            try:
                value = int(value)
            except Exception:
                value = 2
            value = max(0, min(3, int(value)))
        elif key == "selenium_z_order_mode":
            allowed = {"normal", "behind-main", "bottom"}
            as_text = str(value).strip().lower()
            value = as_text if as_text in allowed else "behind-main"
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
        sj.save_key(key, value)
        return {"key": key, "value": sj.cache.get(key)}

    def load_runtime_model(self, model_key: str) -> Dict[str, Any]:
        model_key = self._normalize_model_key(str(model_key))
        if self._model_load_running:
            return {"ok": False, "message": "Another model load is already running"}

        self._model_load_running = True
        sj.save_key("model_mw", model_key)
        sj.save_key("model_f_import", model_key)
        self._runtime_model_key = model_key
        self._runtime_model_loaded = False
        self._runtime_model_message = f"Loading model cache for {model_key}"

        def worker():
            try:
                whisper_load_api = _get_whisper_load_api()
                settings = cast(SettingDict, self.get_settings_snapshot())
                settings["model_mw"] = model_key
                settings["model_f_import"] = model_key
                engine = self._normalize_engine_name(str(settings.get("tl_engine_mw", "Google Translate")))
                tl_engine_whisper = engine in model_values
                is_tc = bool(settings.get("transcribe_mw", True))
                is_tl = bool(settings.get("translate_mw", True))

                model_args = whisper_load_api.get_model_args(settings)
                self.reset_task_state("Model Load")
                self.update_task_message(f"Loading model cache for {model_key}")
                self.update_task_progress(5)
                whisper_load_api.get_model(is_tc, is_tl, tl_engine_whisper, model_key, engine, settings, **model_args)
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

    def get_audio_source_options(self, host_api: Optional[str] = None) -> Dict[str, Any]:
        return self._build_audio_source_options(host_api)

    def get_model_manager_state(self, engine: Optional[str] = None) -> Dict[str, Any]:
        if engine is not None:
            self._model_manager_engine = str(engine)
        return self._build_model_manager_state(engine)

    def check_model(self, model_key: str, engine: str = "whisper") -> Dict[str, Any]:
        engine = str(engine).strip().lower()
        if engine not in {"whisper", "faster-whisper"}:
            engine = "whisper"
        self._model_manager_engine = engine
        self._model_manager_model = model_key

        model_dir = self._resolve_model_dir()
        downloaded, error = self._verify_model_status(engine, model_key, model_dir)
        self._cache_model_status(engine, model_key, downloaded, error, downloading=False)
        state = self._build_model_manager_state(engine)
        state["checked"] = {"model": model_key, "engine": engine, "downloaded": downloaded, "error": error}
        return state

    def check_all_models(self, engine: str = "whisper") -> Dict[str, Any]:
        engine = str(engine).strip().lower()
        if engine not in {"whisper", "faster-whisper", "both"}:
            engine = "whisper"
        if engine in {"whisper", "faster-whisper"}:
            self._model_manager_engine = engine

        model_dir = self._resolve_model_dir()
        engines_to_check = ["whisper", "faster-whisper"] if engine == "both" else [engine]
        for target_engine in engines_to_check:
            for model_key in self._get_model_manager_keys():
                downloaded, error = self._verify_model_status(target_engine, model_key, model_dir)
                self._cache_model_status(target_engine, model_key, downloaded, error, downloading=False)

        return self._build_model_manager_state(self._model_manager_engine, include_both=(engine == "both"))

    def download_model(self, model_key: str, engine: str = "whisper") -> Dict[str, Any]:
        engine = str(engine).strip().lower()
        if engine not in {"whisper", "faster-whisper"}:
            engine = "whisper"

        if self._model_download_running:
            return {"ok": False, "message": "Another model download is already running"}

        self._model_manager_engine = engine
        self._model_manager_model = model_key

        def worker():
            self._model_download_running = True
            try:
                model_dir = self._resolve_model_dir()
                os.makedirs(model_dir, exist_ok=True)

                self.reset_task_state("Model Download")
                self.update_task_message(f"Preparing download for {model_key} ({engine})")
                self.update_task_progress(5)

                # Track current download progress so card-level progress bars can update.
                total_bytes = 0
                observe_path = ""
                if engine == "whisper":
                    from whisper import _MODELS  # pylint: disable=import-outside-toplevel

                    model_url = _MODELS.get(model_key)
                    if model_url is None:
                        raise ValueError(f"Invalid Whisper model key: {model_key}")
                    observe_path = os.path.join(model_dir, os.path.basename(model_url))
                    total_bytes = self._estimate_total_whisper_bytes(model_key)
                else:
                    from faster_whisper.utils import _MODELS as FW_MODELS  # pylint: disable=import-outside-toplevel
                    from huggingface_hub.file_download import repo_folder_name  # pylint: disable=import-outside-toplevel

                    repo_id = FW_MODELS.get(model_key)
                    if repo_id is None:
                        raise ValueError(f"Invalid Faster-Whisper model key: {model_key}")
                    observe_path = os.path.join(model_dir, repo_folder_name(repo_id=repo_id, repo_type="model"))

                self._cache_model_status(
                    engine,
                    model_key,
                    downloaded=False,
                    error="",
                    downloading=True,
                    progress=5,
                    speed="-",
                )

                result_box: Dict[str, Any] = {"ok": False, "error": None}

                def _do_download() -> None:
                    try:
                        if engine == "whisper":
                            from whisper import _MODELS, _download  # pylint: disable=import-outside-toplevel

                            url = _MODELS.get(model_key)
                            if url is None:
                                raise ValueError(f"Invalid Whisper model key: {model_key}")
                            _download(url, model_dir, False)
                        else:
                            from faster_whisper.utils import download_model as fw_download_model  # pylint: disable=import-outside-toplevel

                            fw_download_model(
                                model_key,
                                cache_dir=model_dir,
                            )
                        result_box["ok"] = True
                    except Exception as exc:
                        result_box["error"] = exc

                dl_thread = Thread(target=_do_download, daemon=True)
                dl_thread.start()

                download_started_at = time()
                last_bytes = 0
                last_time = time()
                while dl_thread.is_alive():
                    sleep(0.6)
                    now = time()
                    current_bytes = self._path_size(observe_path)
                    delta_bytes = max(0, current_bytes - last_bytes)
                    delta_t = max(0.2, now - last_time)
                    speed_bps = delta_bytes / delta_t
                    speed_text = f"{self._fmt_bytes(speed_bps)}/s" if speed_bps > 0 else "-"

                    if total_bytes > 0:
                        progress = min(95.0, max(5.0, (current_bytes / total_bytes) * 95.0))
                        size_text = f"{self._fmt_bytes(current_bytes)}/{self._fmt_bytes(total_bytes)}"
                    else:
                        progress = min(95.0, max(5.0, 5.0 + (now - download_started_at) * 0.9))
                        size_text = self._fmt_bytes(current_bytes)

                    self._cache_model_status(
                        engine,
                        model_key,
                        downloaded=False,
                        error="",
                        downloading=True,
                        progress=progress,
                        speed=speed_text,
                    )
                    self.update_task_progress(progress)
                    self.update_task_message(
                        f"Downloading {model_key} ({engine}) | {size_text} | speed {speed_text}"
                    )

                    last_bytes = current_bytes
                    last_time = now

                dl_thread.join()
                if result_box.get("error") is not None:
                    raise cast(Exception, result_box["error"])

                self.update_task_progress(90)
                downloaded = False
                error = ""
                # Hugging Face cache finalization can lag slightly after downloader returns.
                # Retry a few times to avoid false negative verification.
                for _ in range(8):
                    downloaded, error = self._verify_model_status(engine, model_key, model_dir)
                    if downloaded:
                        break
                    sleep(0.5)

                self._cache_model_status(
                    engine,
                    model_key,
                    downloaded,
                    error,
                    downloading=False,
                    progress=100.0 if downloaded else 0.0,
                    speed="-",
                )
                if not downloaded:
                    raise RuntimeError(error or "Download finished but verification failed")

                self.update_task_progress(100)
                self.finish_task(f"Model downloaded: {model_key} ({engine})")
            except Exception as exc:
                logger.exception(exc)
                self._cache_model_status(engine, model_key, False, str(exc), downloading=False)
                self.update_task_error(str(exc))
            finally:
                self._model_download_running = False

        Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "Model download started", "model": model_key, "engine": engine}

    def open_directory(self, name: str) -> Dict[str, str]:
        mapping = {
            "export": self._resolve_export_dir(),
            "log": self._resolve_log_dir(),
            "debug": dir_debug,
            "model": self._resolve_model_dir(),
        }
        target = mapping.get(name)
        if target:
            open_folder(target)
        return {"target": target or ""}

    def select_directory(self, name: str) -> Dict[str, Any]:
        target_map = {
            "export": ("dir_export", self._resolve_export_dir()),
            "model": ("dir_model", self._resolve_model_dir()),
            "selenium_chrome": ("selenium_chrome_user_data_dir", self._resolve_selenium_chrome_user_data_dir()),
        }
        setting_info = target_map.get(str(name or "").strip().lower())
        if setting_info is None:
            return {"ok": False, "message": "Unsupported directory target", "path": ""}

        setting_key, default_dir = setting_info
        window = self.get_window()
        if window is None:
            return {"ok": False, "message": "Window not ready", "path": ""}

        try:
            webview = import_module("webview")
            file_dialog = getattr(getattr(webview, "FileDialog", object), "FOLDER", webview.FOLDER_DIALOG)
            selected = window.create_file_dialog(file_dialog, directory=default_dir)
        except Exception as exc:
            logger.exception(exc)
            return {"ok": False, "message": str(exc), "path": ""}

        if not selected:
            return {"ok": False, "message": "No folder selected", "path": default_dir}

        selected_path = selected[0] if isinstance(selected, (list, tuple)) else selected
        selected_path = str(selected_path or "").strip()
        if not selected_path:
            return {"ok": False, "message": "No folder selected", "path": default_dir}

        sj.save_key(setting_key, selected_path)
        if setting_key == "dir_model":
            self._model_status_cache.clear()

        return {"ok": True, "message": "Directory selected", "path": selected_path, "setting": setting_key}

    def open_link(self, url: str) -> Dict[str, str]:
        open_url(url)
        return {"url": url}

    def notify(self, title: str, message: str) -> Dict[str, str]:
        logger.info(f"{title}: {message}")
        return {"title": title, "message": message}

    def reload_state(self) -> Dict[str, Any]:
        return self.get_state()

    def get_task_state(self) -> Dict[str, Any]:
        return self.snapshot_task_state()

    def get_runtime_model_state(self) -> Dict[str, Any]:
        return self._build_runtime_model_state()

    def get_live_state(self) -> Dict[str, Any]:
        return self.snapshot_live_state()

    def set_recording_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        status_text = str(payload.get("status", "")).lower()
        with self._lock:
            self.recording_state.update(payload)
            if "active" not in payload:
                self.recording_state["active"] = bool(bc.recording)
            # Recording session loads model internally; reflect that in runtime model status.
            if "initializing" in status_text:
                self._model_load_running = True
                self._runtime_model_loaded = False
                if self._runtime_model_key:
                    self._runtime_model_message = f"Loading model cache for {self._runtime_model_key}"
            elif "recording" in status_text or "transcrib" in status_text or "translat" in status_text:
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

    def create_recording_window(self) -> Dict[str, Any]:
        self.detached_window_manager.create_recording_window()
        return {"ok": True}

    def quit_app(self) -> None:
        # Close detached windows first
        self.detached_window_manager.close_all()
        
        tray = self.get_tray()
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        window = self.get_window()
        if window is not None:
            try:
                self._save_main_window_geometry()
                window.destroy()
            except Exception:
                pass

    def import_files(self, files: Optional[list[str]] = None) -> Dict[str, Any]:
        # If stop was just requested, wait for realtime recorder to fully release audio/model resources.
        if not self._wait_recording_idle(timeout_s=12.0):
            return {
                "ok": False,
                "message": "Recording is still cleaning up resources. Please try file import again in a few seconds.",
            }

        if self._model_load_running:
            return {
                "ok": False,
                "message": "Model loading is still in progress. Please wait before starting file import.",
            }

        rec_state = self.get_recording_state()
        rec_status = str(rec_state.get("status", "")).lower()
        rec_active = bool(rec_state.get("active", False)) or bool(bc.recording)
        if rec_active or rec_status in {"stopping...", "initializing recording..."}:
            return {
                "ok": False,
                "message": "Recording is still running or stopping. Please wait a moment before importing files.",
            }

        if not files:
            window = self.get_window()
            if window is None:
                return {"ok": False, "message": "Window not ready"}
            webview = import_module("webview")
            file_dialog = getattr(getattr(webview, "FileDialog", object), "OPEN", webview.OPEN_DIALOG)
            files = window.create_file_dialog(
                file_dialog,
                allow_multiple=True,
                file_types=[
                    "Media Files (*.wav;*.mp3;*.ogg;*.flac;*.aac;*.wma;*.m4a;*.mp4;*.mkv;*.avi;*.mov;*.webm)",
                    "All Files (*.*)",
                ],
            )

        if not files:
            return {"ok": False, "message": "No files selected"}

        from speech_translate.utils.audio import file as audio_file_module
        from speech_translate.utils.whisper.helper import model_keys

        settings = self.get_settings_snapshot()
        lang_source = str(settings.get("source_lang_f_import", "English"))
        lang_target = str(settings.get("target_lang_f_import", "Indonesian"))
        is_tc = bool(settings.get("transcribe_f_import", True))
        is_tl = bool(settings.get("translate_f_import", True))
        engine = str(settings.get("tl_engine_f_import", "Google Translate"))
        model_name_tc = self._normalize_model_key(str(settings.get("model_f_import", "")))
        engine = self._normalize_model_key(engine)
        needs_runtime_model = bool(is_tc) or (bool(is_tl) and engine in model_keys)

        if needs_runtime_model:
            same_model_loaded = bool(self._runtime_model_loaded) and self._runtime_model_key == model_name_tc
            self._runtime_model_key = model_name_tc
            if same_model_loaded:
                self._model_load_running = False
                self._runtime_model_message = f"Model ready: {model_name_tc}"
            else:
                self._runtime_model_loaded = False
                self._runtime_model_message = f"Loading model cache for {model_name_tc}"
                self._model_load_running = True

        self.reset_task_state("File Import")
        self.bind_headless_main_window()

        # Replace tkinter-dependent dialogs in this module with headless adapters.
        audio_file_module.FileProcessDialog = (
            lambda master, title, mode, headers: HeadlessFileProcessDialog(master, title, mode, headers, bridge=self)
        )
        audio_file_module.mbox = headless_mbox

        def worker():
            try:
                bc.enable_file_process()
                audio_file_module.process_file(list(files), model_name_tc, lang_source, lang_target, is_tc, is_tl, engine)
                summary_parts = []
                if is_tc:
                    summary_parts.append(f"{bc.file_tced_counter} transcribed")
                if is_tl:
                    summary_parts.append(f"{bc.file_tled_counter} translated")
                summary = ", ".join(summary_parts) if summary_parts else "no output generated"
                self.finish_task(f"File import finished: {summary}")
                if needs_runtime_model:
                    self._runtime_model_loaded = True
                    self._runtime_model_message = f"Model ready: {model_name_tc}"
            except Exception as exc:
                logger.exception(exc)
                logger.error(f"File import failed: {exc}")
                self.update_task_error(str(exc))
                if needs_runtime_model:
                    self._runtime_model_loaded = False
                    self._runtime_model_message = f"Model load failed: {exc}"
            finally:
                bc.disable_file_process()
                if needs_runtime_model:
                    self._model_load_running = False
                auto_close_selenium = bool(self.get_settings_snapshot().get("selenium_auto_close_on_task_done", True))
                if auto_close_selenium and is_tl and engine == "Selenium Chrome Translate":
                    shutdown_selenium_translator()

        Thread(target=worker, daemon=True).start()
        return {
            "ok": True,
            "count": len(files),
            "engine_whisper": engine in model_keys,
            "message": "File import started",
        }

    def get_detached_config(self, mode: str) -> Dict[str, Any]:
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        return {
            "font": sj.cache.get(f"tb_ex_{mode}_font", "Arial"),
            "font_size": sj.cache.get(f"tb_ex_{mode}_font_size", 13),
            "font_bold": sj.cache.get(f"tb_ex_{mode}_font_bold", True),
            "font_color": sj.cache.get(f"tb_ex_{mode}_font_color", "#FFFFFF"),
            "bg_color": sj.cache.get(f"tb_ex_{mode}_bg_color", "#000000"),
            "always_on_top": sj.cache.get(f"ex_{mode}_always_on_top", 0),
            "no_title_bar": sj.cache.get(f"ex_{mode}_no_title_bar", 0),
            "opacity": sj.cache.get(f"ex_{mode}_opacity", 1.0),
            "click_through": sj.cache.get(f"ex_{mode}_click_through", 0),
        }

    def set_detached_config(self, mode: str, key: str, value: Any) -> Dict[str, Any]:
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        setting_key = f"ex_{mode}_{key}" if key in ("always_on_top", "no_title_bar", "opacity", "click_through") else f"tb_ex_{mode}_{key}"
        sj.save_key(setting_key, value)
        return {"key": setting_key, "value": sj.cache.get(setting_key)}

    def create_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        """Create a detached subtitle window."""
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        
        raw_geometry = sj.cache.get(f"ex_{mode}_geometry", "900x240")
        width, height = _parse_window_size(raw_geometry, 900, 240)
        logger.info(
            f"[DetachedGeometry][open-request] mode={mode} "
            f"cache={raw_geometry} parsed={width}x{height}"
        )
        if x is None or y is None:
            x, y = _center_window_pos(width, height)

        x, y = _ensure_visible_or_center(int(x), int(y), int(width), int(height))

        self.detached_window_manager.create_window(mode, x, y, width, height)

        # Always apply current detached config when window is opened from any entry point.
        self.update_detached_config(mode)

        # Push current detached text immediately so opening from top buttons is consistent.
        live_state = self.snapshot_live_state()
        if mode == "tc":
            html_content = live_state.get("detached_transcribed_html") or live_state.get("detached_transcribed_text") or ""
        else:
            html_content = live_state.get("detached_translated_html") or live_state.get("detached_translated_text") or ""
        if html_content:
            self.update_detached_content(mode, str(html_content))

        return {"status": "created", "mode": mode}

    def toggle_detached_window(self, mode: str = "tc", x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        """Open a detached window, or close it if it is already open."""
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"

        if mode in self.detached_window_manager.windows:
            self.detached_window_manager.close_window(mode)
            return {"status": "closed", "mode": mode}

        return self.create_detached_window(mode, x, y)

    def show_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        """Show a detached window."""
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        
        self.detached_window_manager.show_window(mode)
        return {"status": "shown", "mode": mode}

    def hide_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        """Hide a detached window."""
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        
        self.detached_window_manager.hide_window(mode)
        return {"status": "hidden", "mode": mode}

    def close_detached_window(self, mode: str = "tc") -> Dict[str, Any]:
        """Close a detached window."""
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        
        self.detached_window_manager.close_window(mode)
        return {"status": "closed", "mode": mode}

    def update_detached_content(self, mode: str, html_content: str) -> Dict[str, Any]:
        """Update content in a detached window."""
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        
        self.detached_window_manager.update_window_content(mode, html_content)
        return {"status": "updated", "mode": mode}

    def update_detached_config(self, mode: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Update configuration for a detached window."""
        mode = str(mode).lower()
        if mode not in {"tc", "tl"}:
            mode = "tl"
        
        # If no config provided, get the full config
        if config is None:
            config = self.get_detached_config(mode)
        
        self.detached_window_manager.update_window_config(mode, config)
        return {"status": "config_updated", "mode": mode}

    def start_recording(
        self,
        device: str = "mic",
        lang_source: str = "English",
        lang_target: str = "Indonesian",
        engine: str = "Selenium Chrome Translate",
        is_tc: bool = True,
        is_tl: bool = True,
    ) -> Dict[str, Any]:
        """Start recording from microphone or speaker.
        
        Parameters
        ----------
        device : str
            'mic' or 'speaker'
        lang_source : str
            Source language for transcription
        lang_target : str
            Target language for translation
        engine : str
            Translation engine to use
        is_tc : bool
            Whether to transcribe
        is_tl : bool
            Whether to translate
        
        Returns
        -------
        Dict with status information
        """
        from speech_translate.utils.audio.record import record_session
        from speech_translate.utils.whisper.helper import model_keys

        # Check if already recording
        if bc.recording:
            return {"ok": False, "message": "Already recording"}

        # Get settings and normalize values
        settings = self.get_settings_snapshot()
        lang_source = str(settings.get("source_lang_mw", lang_source))
        lang_target = str(settings.get("target_lang_mw", lang_target))
        device = str(settings.get("input", device))
        engine = str(settings.get("tl_engine_mw", engine))
        is_tc = bool(settings.get("transcribe_mw", is_tc))
        is_tl = bool(settings.get("translate_mw", is_tl))
        model_name_tc = self._normalize_model_key(str(settings.get("model_mw", "")))
        engine = self._normalize_engine_name(engine)
        self._runtime_model_key = model_name_tc

        cached_bundle = False
        try:
            whisper_load_api = _get_whisper_load_api()
            model_args = whisper_load_api.get_model_args(cast(SettingDict, settings))
            cached_bundle = whisper_load_api.is_model_bundle_cached(
                is_tc,
                is_tl,
                engine in model_values,
                model_name_tc,
                engine,
                cast(SettingDict, settings),
                **model_args,
            )
        except Exception:
            cached_bundle = False

        if cached_bundle:
            self._model_load_running = False
            self._runtime_model_loaded = True
            self._runtime_model_message = f"Model ready: {self._runtime_model_key}"

        if not is_tc and not is_tl:
            return {"ok": False, "message": "Please enable Transcribe or Translate before starting recording"}

        # Bind headless main window for callbacks
        self.bind_headless_main_window()
        
        # Clear previous transcription/translation by directly clearing the data structures
        bc.tc_sentences = []
        bc.tl_sentences = []
        self.clear_live()
        
        # Enable recording flag
        bc.enable_rec()
        self.reset_task_state("Recording")
        self.set_recording_state(
            {
                "status": "Preparing recording..." if cached_bundle else "Initializing recording...",
                "active": True,
                "device": device,
                "lang_source": lang_source,
                "lang_target": lang_target,
                "engine": engine,
                "mode": "Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate",
                "timer": "00:00:00",
                "buffer": "0/0 sec",
                "sentences": "0",
            }
        )
        
        # Replace tkinter-dependent dialogs in record module
        # Make mbox always return True (continue) to skip confirmation dialogs
        import speech_translate.utils.audio.record as record_module
        record_module.mbox = lambda *args, **kwargs: True

        def worker():
            try:
                speaker = device.lower() == "speaker"
                record_session(lang_source, lang_target, engine, model_name_tc, device, is_tc, is_tl, speaker)
                self.finish_task("Recording finished")
            except Exception as exc:
                logger.exception(exc)
                logger.error(f"Recording failed: {exc}")
                self.update_task_error(str(exc))
            finally:
                bc.disable_rec()
                self.set_recording_state({"status": "Stopped", "active": False})
                auto_close_selenium = bool(self.get_settings_snapshot().get("selenium_auto_close_on_task_done", True))
                if auto_close_selenium and is_tl and engine == "Selenium Chrome Translate":
                    shutdown_selenium_translator()
                self._record_worker_thread = None

        self._record_worker_thread = Thread(target=worker, daemon=True)
        self._record_worker_thread.start()
        return {
            "ok": True,
            "device": device,
            "engine_whisper": engine in model_keys,
            "message": "Recording started",
        }

    def stop_recording(self) -> Dict[str, Any]:
        """Stop the current recording session.
        
        Returns
        -------
        Dict with status information
        """
        if not bc.recording:
            return {"ok": False, "message": "Not currently recording"}

        self.set_recording_state({"status": "Stopping...", "active": False})
        bc.disable_rec()
        # Wait for full teardown so next actions (e.g., file import) won't contend with stale resources.
        settled = self._wait_recording_idle(timeout_s=12.0)
        if settled:
            self.set_recording_state({"status": "Stopped", "active": False})
            engine = self._normalize_engine_name(str(self.get_settings_snapshot().get("tl_engine_mw", "")))
            auto_close_selenium = bool(self.get_settings_snapshot().get("selenium_auto_close_on_task_done", True))
            if auto_close_selenium and engine == "Selenium Chrome Translate":
                shutdown_selenium_translator()
            return {"ok": True, "message": "Recording stopped"}

        return {
            "ok": True,
            "message": "Stop requested; cleanup is still finishing in background",
        }

    def update_recording_popup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update dedicated recording popup window."""
        self.set_recording_state(payload)
        self.detached_window_manager.update_recording_status(payload)
        return {"ok": True}


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
