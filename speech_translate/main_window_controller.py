from __future__ import annotations

from threading import Lock
from time import time
from typing import Optional

from loguru import logger

from speech_translate.controller_protocols import MainWindowBridge, SettingsStore, TrayLike, FolderDialogWindow


class MainWindowController:
    """Owns main-window startup markers and geometry persistence."""

    def __init__(self, bridge: MainWindowBridge, settings: SettingsStore):
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

    def mark_startup(self, marker: str) -> dict[str, object]:
        label = str(marker or "").strip() or "unknown"
        self.log_startup_marker(f"js_{label}")
        return {"ok": True, "marker": label}

    def bind_window(self, window: FolderDialogWindow) -> None:
        self.log_startup_marker("bind_window")
        try:
            self._bind_window_events(window)
        except Exception:
            pass

    def _bind_window_events(self, window: FolderDialogWindow) -> None:
        if not hasattr(window, "events"):
            return
        if hasattr(window.events, "shown"):
            window.events.shown += lambda *_: self.on_main_window_shown(window)
        if hasattr(window.events, "loaded"):
            window.events.loaded += lambda *_: self.log_startup_marker("main_window_loaded")
        if hasattr(window.events, "closed"):
            window.events.closed += lambda *_: self.save_main_window_geometry(force=True)

    def on_main_window_shown(self, window: FolderDialogWindow) -> None:
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

    def _resolve_scale_factor(self, native_window: object | None) -> float:
        if native_window is None:
            return 1.0
        try:
            scale_factor = float(getattr(native_window, "scale_factor", 1.0) or 1.0)
            if scale_factor > 0:
                return scale_factor
        except Exception:
            pass
        return 1.0

    def _extract_native_window_size(self, native_window: object | None, *, scale_factor: float) -> tuple[int | None, int | None, int | None, int | None]:
        if native_window is None:
            return None, None, None, None
        try:
            client_size = getattr(native_window, "ClientSize", None)
            if client_size is None:
                return None, None, None, None
            raw_width = int(getattr(client_size, "Width"))
            raw_height = int(getattr(client_size, "Height"))
            width = int(round(raw_width / scale_factor))
            height = int(round(raw_height / scale_factor))
            return width, height, raw_width, raw_height
        except Exception:
            return None, None, None, None

    def _extract_window_size(self, window: FolderDialogWindow) -> tuple[int | None, int | None]:
        try:
            return int(getattr(window, "width")), int(getattr(window, "height"))
        except Exception:
            return None, None

    def _save_geometry_if_changed(self, geometry: str, *, force: bool) -> bool:
        with self.main_geometry_lock:
            if not force and geometry == self.main_geometry_last_saved:
                return False
            self.main_geometry_last_saved = geometry
            self.settings.save_key("mw_size", geometry)
        return True

    def save_main_window_geometry(self, force: bool = False) -> None:
        window = self.bridge.get_window()
        if window is None:
            return
        native_window = getattr(window, "native", None)
        scale_factor = self._resolve_scale_factor(native_window)
        width, height, raw_width, raw_height = self._extract_native_window_size(native_window, scale_factor=scale_factor)
        if width is None or height is None:
            width, height = self._extract_window_size(window)
            raw_width, raw_height = None, None
        if width is None or height is None:
            return

        if width >= 600 and height >= 300:
            geometry = f"{width}x{height}"
            if not self._save_geometry_if_changed(geometry, force=force):
                return
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
