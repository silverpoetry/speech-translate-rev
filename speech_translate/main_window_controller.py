from __future__ import annotations

from threading import Lock
from time import time
from typing import Any, Optional

from loguru import logger


class MainWindowController:
    """Owns main-window startup markers and geometry persistence."""

    def __init__(self, bridge: Any, settings: Any):
        self.bridge = bridge
        self.settings = settings
        self.startup_t0: Optional[float] = None
        self.first_state_logged = False
        self.main_window_show_allowed = False
        self.main_geometry_lock = Lock()
        self.main_geometry_last_saved = ""

    def set_startup_t0(self, started_at: float) -> None:
        self.startup_t0 = started_at

    def log_startup_marker(self, marker: str) -> None:
        if self.startup_t0 is None:
            logger.debug(f"[Startup] {marker}")
            return
        elapsed_ms = int((time() - self.startup_t0) * 1000)
        logger.debug(f"[Startup] +{elapsed_ms}ms {marker}")

    def mark_startup(self, marker: str) -> dict[str, Any]:
        label = str(marker or "").strip() or "unknown"
        self.log_startup_marker(f"js_{label}")
        return {"ok": True, "marker": label}

    def bind_window(self, window) -> None:
        self.log_startup_marker("bind_window")
        try:
            if hasattr(window, "events"):
                if hasattr(window.events, "shown"):
                    window.events.shown += lambda *_: self.on_main_window_shown(window)
                if hasattr(window.events, "loaded"):
                    window.events.loaded += lambda *_: self.log_startup_marker("main_window_loaded")
                if hasattr(window.events, "closed"):
                    window.events.closed += lambda *_: self.save_main_window_geometry(force=True)
        except Exception:
            pass

    def on_main_window_shown(self, window) -> None:
        if not self.main_window_show_allowed:
            try:
                window.hide()
            except Exception:
                pass
        self.log_startup_marker("main_window_shown")

    def show_main_window(self) -> None:
        self.main_window_show_allowed = True
        window = self.bridge.get_window()
        if not window:
            return
        try:
            window.show()
        except Exception:
            return
        try:
            window.bring_to_front()
        except Exception:
            pass
        self.log_startup_marker("main_window_shown_after_init")

    def save_main_window_geometry(self, force: bool = False) -> None:
        window = self.bridge.get_window()
        if window is None:
            return
        native_window = getattr(window, "native", None)

        width = None
        height = None
        raw_width = None
        raw_height = None
        scale_factor = 1.0
        if native_window is not None:
            try:
                scale_factor = float(getattr(native_window, "scale_factor", 1.0) or 1.0)
                if scale_factor <= 0:
                    scale_factor = 1.0
            except Exception:
                pass

        if native_window is not None:
            try:
                client_size = getattr(native_window, "ClientSize", None)
                if client_size is not None:
                    raw_width = int(getattr(client_size, "Width"))
                    raw_height = int(getattr(client_size, "Height"))
                    width = int(round(raw_width / scale_factor))
                    height = int(round(raw_height / scale_factor))
            except Exception:
                pass

        if width is None or height is None:
            try:
                width = int(getattr(window, "width"))
                height = int(getattr(window, "height"))
            except Exception:
                return

        if width >= 600 and height >= 300:
            geometry = f"{width}x{height}"
            with self.main_geometry_lock:
                if not force and geometry == self.main_geometry_last_saved:
                    return
                self.main_geometry_last_saved = geometry
                self.settings.save_key("mw_size", geometry)
            logger.info(
                f"[MainGeometry][save] logical={geometry} raw_client={raw_width}x{raw_height} "
                f"scale_factor={scale_factor:.3f} force={force}"
            )

    def quit_app(self) -> None:
        self.bridge.detached_window_manager.close_all()
        if tray := self.bridge.get_tray():
            try:
                tray.stop()
            except Exception:
                pass
        if window := self.bridge.get_window():
            try:
                self.save_main_window_geometry()
                window.destroy()
            except Exception:
                pass
