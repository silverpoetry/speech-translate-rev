from __future__ import annotations

import ctypes
import time
import weakref
from pathlib import Path

from speech_translate._path import p_app_icon
from speech_translate.controller_protocols import AppTrayBridge
from speech_translate.log_helpers import logger
from speech_translate.webview_runtime import load_webview_runtime


_tray_panel_owner_ref: weakref.ReferenceType["AppTray"] | None = None


def _bind_tray_panel_owner(tray: "AppTray") -> None:
    global _tray_panel_owner_ref
    _tray_panel_owner_ref = weakref.ref(tray)


def _get_tray_panel_owner() -> "AppTray":
    tray = _tray_panel_owner_ref() if _tray_panel_owner_ref is not None else None
    if tray is None:
        raise RuntimeError("Tray panel owner is unavailable")
    return tray


class TrayPanelApi:
    __slots__ = ()

    def show_app(self):
        tray = _get_tray_panel_owner()
        tray.hide_panel()
        tray.show_app()
        return {"ok": True}

    def open_directory(self, name: str):
        tray = _get_tray_panel_owner()
        tray.hide_panel()
        return tray.bridge.open_directory(name)

    def hide_panel(self):
        _get_tray_panel_owner().hide_panel()
        return {"ok": True}

    def quit_app(self):
        tray = _get_tray_panel_owner()
        tray.hide_panel()
        tray.exit_app()
        return {"ok": True}


class AppTray:
    PANEL_WIDTH = 158
    PANEL_HEIGHT = 172

    def __init__(self, bridge: AppTrayBridge):
        self.bridge = bridge
        self.icon = None
        self.panel_window = None
        self._panel_destroying = False
        self._panel_native_settings_applied = False
        self._panel_blur_handler_bound = False
        self._panel_ignore_deactivate_until = 0.0
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

        self.icon = pystray.Icon("Speech Translate", ico, "Speech Translate")
        self.icon.run_detached()
        self._install_pointer_actions()

    def _install_pointer_actions(self) -> None:
        if self.icon is None or not hasattr(self.icon, "_message_handlers"):
            return

        from pystray._util import win32

        original = self.icon._message_handlers.get(win32.WM_NOTIFY)
        left_double_click = getattr(win32, "WM_LBUTTONDBLCLK", 0x0203)

        def _patched_on_notify(wparam, lparam):
            if lparam == win32.WM_RBUTTONUP:
                self.open_panel()
                return None
            if lparam in (win32.WM_LBUTTONUP, left_double_click):
                self.show_app()
                return None
            if original is not None:
                return original(wparam, lparam)
            return None

        self.icon._message_handlers[win32.WM_NOTIFY] = _patched_on_notify

    @staticmethod
    def _screen_scale_factor() -> float:
        try:
            scale = float(ctypes.windll.shcore.GetScaleFactorForDevice(0)) / 100.0
            if scale > 0:
                return scale
        except Exception:
            pass
        return 1.0

    @staticmethod
    def _cursor_position_physical() -> tuple[int, int]:
        try:
            from ctypes import wintypes

            point = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            return int(point.x), int(point.y)
        except Exception:
            return 1200, 800

    def _cursor_position(self) -> tuple[int, int]:
        scale = self._screen_scale_factor()
        x, y = self._cursor_position_physical()
        return int(round(x / scale)), int(round(y / scale))

    def _panel_placement(self, width: int, height: int) -> tuple[int, int]:
        x, y = self._cursor_position()
        return max(12, x - width + 24), max(12, y - height - 14)

    def _run_on_ui_thread(self, callback):
        window = self.bridge.get_window()
        native = getattr(window, "native", None) if window is not None else None
        if native is None or not getattr(native, "InvokeRequired", False):
            return callback()

        import clr

        clr.AddReference("System")
        from System import Action

        result_box = {"value": None}
        error_box = {"error": None}

        def _wrapped():
            try:
                result_box["value"] = callback()
            except Exception as exc:
                error_box["error"] = exc

        native.Invoke(Action(_wrapped))
        if error_box["error"] is not None:
            raise error_box["error"]
        return result_box["value"]

    def _bind_panel_events(self, window) -> None:
        try:
            if hasattr(window, "events") and hasattr(window.events, "closed"):
                window.events.closed += lambda *_: self._on_panel_closed()
            if hasattr(window, "events") and hasattr(window.events, "loaded"):
                window.events.loaded += lambda *_: self._on_panel_loaded()
        except Exception:
            pass

    def _on_panel_closed(self):
        self._detach_panel_native_handlers()
        self.panel_window = None
        self._panel_destroying = False
        self._panel_native_settings_applied = False
        self._panel_ignore_deactivate_until = 0.0

    def _on_panel_native_deactivate(self, *_args) -> None:
        if self.panel_window is None or self._panel_destroying:
            return
        if time.monotonic() < self._panel_ignore_deactivate_until:
            return
        logger.debug("[Tray] panel lost focus; hiding")
        self.hide_panel()

    def _detach_panel_native_handlers(self) -> None:
        if not self._panel_blur_handler_bound or self.panel_window is None:
            self._panel_blur_handler_bound = False
            return
        native = getattr(self.panel_window, "native", None)
        if native is not None:
            try:
                native.Deactivate -= self._on_panel_native_deactivate
            except Exception:
                pass
        self._panel_blur_handler_bound = False

    def _on_panel_loaded(self) -> None:
        self._apply_panel_native_settings_when_ready()
        if self.panel_window is None:
            return
        self._panel_ignore_deactivate_until = time.monotonic() + 0.25
        try:
            self.panel_window.show()
        except Exception:
            logger.exception("Failed to show tray panel after load")
        try:
            native = getattr(self.panel_window, "native", None)
            if native is not None and hasattr(native, "Activate"):
                self._run_on_ui_thread(lambda: native.Activate())
        except Exception:
            logger.exception("Failed to activate tray panel after load")

    def _apply_panel_native_settings_when_ready(self) -> None:
        if self.panel_window is None or self._panel_native_settings_applied:
            return
        try:
            self._run_on_ui_thread(lambda: self._apply_panel_native_settings(self.panel_window))
            self._panel_native_settings_applied = True
        except Exception:
            logger.exception("Failed to apply tray panel native settings")

    def _apply_panel_native_settings(self, window) -> None:
        native = getattr(window, "native", None)
        if native is None:
            return

        try:
            import clr

            clr.AddReference("System.Drawing")
            from System.Drawing import Region, Size
            from System.Drawing.Drawing2D import GraphicsPath

            scale_factor = float(getattr(native, "scale_factor", 1.0) or 1.0)
            client_width = int(round(self.PANEL_WIDTH * scale_factor))
            client_height = int(round(self.PANEL_HEIGHT * scale_factor))
            fixed_size = Size(client_width, client_height)
            native.MinimumSize = fixed_size
            native.MaximumSize = fixed_size
            native.ClientSize = fixed_size
            radius = max(8, int(round(10 * scale_factor)))
            diameter = min(client_width, client_height, radius * 2)
            path = GraphicsPath()
            path.AddArc(0, 0, diameter, diameter, 180, 90)
            path.AddArc(client_width - diameter, 0, diameter, diameter, 270, 90)
            path.AddArc(client_width - diameter, client_height - diameter, diameter, diameter, 0, 90)
            path.AddArc(0, client_height - diameter, diameter, diameter, 90, 90)
            path.CloseFigure()
            native.Region = Region(path)
            logger.info(
                f"[Tray] sync_panel_size logical={self.PANEL_WIDTH}x{self.PANEL_HEIGHT} "
                f"raw_client={client_width}x{client_height} scale={scale_factor:.3f}"
            )
        except Exception:
            logger.exception("Failed to sync tray panel client size")

        if not self._panel_blur_handler_bound and hasattr(native, "Deactivate"):
            try:
                native.Deactivate += self._on_panel_native_deactivate
                self._panel_blur_handler_bound = True
            except Exception:
                logger.exception("Failed to bind tray panel blur handler")

        try:
            native.ShowInTaskbar = False
        except Exception:
            pass

        try:
            native.TopMost = True
        except Exception:
            pass

        try:
            native.FormBorderStyle = 0
        except Exception:
            pass

        try:
            native.ControlBox = False
        except Exception:
            pass

        try:
            native.MinimizeBox = False
        except Exception:
            pass

        try:
            native.MaximizeBox = False
        except Exception:
            pass

    def _create_panel_window(self):
        if self.panel_window is not None:
            return self.panel_window

        webview = load_webview_runtime()
        html_path = str(Path(__file__).with_name("web") / "tray_panel.html")
        x, y = self._panel_placement(self.PANEL_WIDTH, self.PANEL_HEIGHT)
        logger.debug(f"[Tray] create_panel x={x} y={y} scale={self._screen_scale_factor():.3f}")
        _bind_tray_panel_owner(self)
        self.panel_window = webview.create_window(
            "Speech Translate",
            html_path,
            js_api=TrayPanelApi(),
            width=self.PANEL_WIDTH,
            height=self.PANEL_HEIGHT,
            x=x,
            y=y,
            resizable=False,
            min_size=(120, 120),
            hidden=True,
            frameless=True,
            easy_drag=False,
            shadow=True,
            background_color="#f8fafc",
            on_top=True,
        )
        self._bind_panel_events(self.panel_window)
        return self.panel_window

    def open_panel(self, *_args):
        try:
            if self.panel_window is None:
                self._create_panel_window()
                return

            x, y = self._panel_placement(self.PANEL_WIDTH, self.PANEL_HEIGHT)
            logger.debug(f"[Tray] reopen_panel x={x} y={y} scale={self._screen_scale_factor():.3f}")
            try:
                self._panel_ignore_deactivate_until = time.monotonic() + 0.18
                self.panel_window.move(x, y)
            except Exception:
                logger.exception("Failed to move tray panel")
            try:
                self._run_on_ui_thread(lambda: self._apply_panel_native_settings(self.panel_window))
                native = getattr(self.panel_window, "native", None)
                if native is not None and hasattr(native, "Activate"):
                    self._run_on_ui_thread(lambda: native.Activate())
            except Exception:
                logger.exception("Failed to activate tray panel")
        except Exception:
            logger.exception("Failed to open tray panel")

    def show_app(self, *_args):
        self.hide_panel()
        window = self.bridge.get_window()
        if window is not None:
            try:
                if hasattr(window, "restore"):
                    window.restore()
            except Exception:
                pass
            try:
                window.show()
            except Exception:
                pass
            try:
                window.bring_to_front()
            except Exception:
                pass

    def hide_panel(self):
        if self.panel_window is None or self._panel_destroying:
            return

        try:
            self._panel_destroying = True
            self.panel_window.destroy()
        except Exception:
            logger.exception("Failed to destroy tray panel")
            self.panel_window = None
            self._panel_destroying = False

    def stop(self):
        self.hide_panel()
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def exit_app(self, *_args):
        self.bridge.quit_app()
