from __future__ import annotations

from typing import Any

from speech_translate._path import p_app_icon
from speech_translate._version import __version__
from speech_translate.utils.helper import open_url


class AppTray:
    """System tray integration for the webview app."""

    def __init__(self, bridge: Any):
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
