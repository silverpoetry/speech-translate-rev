from __future__ import annotations

from pathlib import Path

from speech_translate._path import p_app_icon
from speech_translate.controller_protocols import AppTrayBridge
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
    """System tray integration for the webview app."""

    def __init__(self, bridge: AppTrayBridge):
        self.bridge = bridge
        self.icon = None
        self.panel_window = None
        self._panel_destroying = False
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
    def _cursor_position() -> tuple[int, int]:
        try:
            import ctypes
            from ctypes import wintypes

            point = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            return int(point.x), int(point.y)
        except Exception:
            return 1200, 800

    def _panel_placement(self, width: int, height: int) -> tuple[int, int]:
        x, y = self._cursor_position()
        return max(12, x - width + 28), max(12, y - height - 18)

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

    def _ensure_panel(self):
        if self.panel_window is not None:
            return self.panel_window

        webview = load_webview_runtime()
        html_path = str(Path(__file__).with_name("web") / "tray_panel.html")
        width, height = 320, 264
        x, y = self._panel_placement(width, height)
        self.panel_window = webview.create_window(
            "Speech Translate",
            html_path,
            js_api=TrayPanelApi(self),
            width=width,
            height=height,
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
        return self.panel_window

    def open_panel(self, *_args):
        window = self._ensure_panel()
        if window is None:
            return
        x, y = self._panel_placement(320, 264)
        try:
            if hasattr(window, "move"):
                window.move(x, y)
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

    def show_app(self, *_args):
        self.hide_panel()
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

    def hide_panel(self):
        if self.panel_window is None:
            return
        try:
            self.panel_window.hide()
        except Exception:
            pass

    def stop(self):
        if self.panel_window is not None:
            try:
                self._panel_destroying = True
                self.panel_window.destroy()
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
