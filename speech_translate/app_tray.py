from __future__ import annotations

import ctypes
from pathlib import Path

from speech_translate._path import p_app_icon
from speech_translate.controller_protocols import AppTrayBridge
from speech_translate.log_helpers import logger
from speech_translate.webview_runtime import load_webview_runtime


class TrayPanelApi:
    def __init__(self, tray: "AppTray"):
        self._show_app = tray.show_app
        self._open_directory = tray.bridge.open_directory
        self._hide_panel = tray.hide_panel
        self._quit_app = tray.exit_app

    def show_app(self):
        self._hide_panel()
        self._show_app()
        return {"ok": True}

    def open_directory(self, name: str):
        self._hide_panel()
        return self._open_directory(name)

    def hide_panel(self):
        self._hide_panel()
        return {"ok": True}

    def quit_app(self):
        self._hide_panel()
        self._quit_app()
        return {"ok": True}


class AppTray:
    PANEL_WIDTH = 336
    PANEL_HEIGHT = 272

    def __init__(self, bridge: AppTrayBridge):
        self.bridge = bridge
        self.icon = None
        self.panel_window = None
        self._panel_destroying = False
        self._create_tray()
        self._prewarm_panel()

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

    def _bind_panel_events(self, window) -> None:
        try:
            if hasattr(window, "events") and hasattr(window.events, "closing"):
                window.events.closing += lambda *_: self._on_panel_closing()
            if hasattr(window, "events") and hasattr(window.events, "closed"):
                window.events.closed += lambda *_: self._on_panel_closed()
        except Exception:
            pass

    def _on_panel_closing(self):
        if self._panel_destroying:
            return None
        self.hide_panel()
        return False

    def _on_panel_closed(self):
        self.panel_window = None

    def _run_on_ui_thread(self, callback):
        window = self.bridge.get_window()
        native = getattr(window, "native", None) if window is not None else None
        if native is None:
            return callback()
        try:
            if getattr(native, "InvokeRequired", False):
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
        except Exception:
            logger.exception("Failed to marshal tray callback onto UI thread")
            raise
        return callback()

    def _apply_panel_native_settings(self, window) -> None:
        native = getattr(window, "native", None)
        if native is None:
            return

        try:
            native.ShowInTaskbar = False
        except Exception:
            pass

        try:
            native.TopMost = True
        except Exception:
            pass

        try:
            hwnd = int(native.Handle.ToInt32())
            ex_style = int(ctypes.windll.user32.GetWindowLongW(hwnd, -20))
            ex_style |= 0x00000080  # WS_EX_TOOLWINDOW
            ex_style &= ~0x00040000  # WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style)
            ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass

    def _activate_panel_native(self, window) -> None:
        native = getattr(window, "native", None)
        if native is None:
            return
        try:
            native.Activate()
        except Exception:
            pass

    def _ensure_panel(self):
        if self.panel_window is not None:
            return self.panel_window

        def _create_panel():
            if self.panel_window is not None:
                return self.panel_window
            webview = load_webview_runtime()
            html_path = str(Path(__file__).with_name("web") / "tray_panel.html")
            x, y = self._panel_placement(self.PANEL_WIDTH, self.PANEL_HEIGHT)
            self.panel_window = webview.create_window(
                "Speech Translate",
                html_path,
                js_api=TrayPanelApi(self),
                width=self.PANEL_WIDTH,
                height=self.PANEL_HEIGHT,
                x=x,
                y=y,
                resizable=False,
                hidden=True,
                frameless=True,
                easy_drag=False,
                on_top=True,
                shadow=True,
            )
            self._bind_panel_events(self.panel_window)
            self._apply_panel_native_settings(self.panel_window)
            return self.panel_window

        return self._run_on_ui_thread(_create_panel)

    def _prewarm_panel(self) -> None:
        window = self.bridge.get_window()
        if window is None or getattr(window, "native", None) is None:
            return
        try:
            self._ensure_panel()
            logger.debug("[Tray] panel prewarmed")
        except Exception:
            logger.exception("Failed to prewarm tray panel")

    def open_panel(self, *_args):
        try:
            def _show_panel():
                window = self._ensure_panel()
                if window is None:
                    return
                x, y = self._panel_placement(self.PANEL_WIDTH, self.PANEL_HEIGHT)
                logger.debug(f"[Tray] open_panel x={x} y={y} scale={self._screen_scale_factor():.3f}")
                try:
                    window.move(x, y)
                except Exception:
                    logger.exception("Failed to move tray panel")
                try:
                    self._apply_panel_native_settings(window)
                    if hasattr(window, "restore"):
                        window.restore()
                    window.show()
                    self._activate_panel_native(window)
                except Exception:
                    logger.exception("Failed to show tray panel")

            self._run_on_ui_thread(_show_panel)
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
        if self.panel_window is None:
            return
        try:
            self._run_on_ui_thread(lambda: self.panel_window.hide() if self.panel_window is not None else None)
        except Exception:
            pass

    def stop(self):
        if self.panel_window is not None:
            try:
                def _destroy():
                    if self.panel_window is None:
                        return
                    self._panel_destroying = True
                    self.panel_window.destroy()

                self._run_on_ui_thread(_destroy)
            except Exception:
                pass
            self.panel_window = None
            self._panel_destroying = False
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def exit_app(self, *_args):
        self.bridge.quit_app()
