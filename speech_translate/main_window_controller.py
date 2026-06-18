from __future__ import annotations

from threading import Lock
from time import time
from typing import Optional

from speech_translate.controller_protocols import MainWindowBridge, SettingsStore, TrayLike, FolderDialogWindow
from speech_translate.log_helpers import logger
from speech_translate.window_geometry import (
    extract_window_placement,
    format_window_position,
    format_window_size,
)
from speech_translate.window_lifecycle import (
    get_target_placement,
    is_preloaded_window,
    reveal_preloaded_window,
    should_skip_preloaded_geometry_save,
)


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
        self.quit_in_progress = False

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
        if hasattr(window.events, "closing"):
            window.events.closing += lambda *_: self.on_main_window_closing(window)
        if hasattr(window.events, "closed"):
            window.events.closed += lambda *_: self.save_main_window_geometry(force=True)

    def on_main_window_shown(self, window: FolderDialogWindow) -> None:
        if not self.main_window_show_allowed and not is_preloaded_window(window):
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
        if is_preloaded_window(window):
            try:
                reveal_preloaded_window(window, bring_to_front=True)
            except Exception:
                logger.exception("[Startup] failed to reveal preloaded main window")
                return
        else:
            if get_target_placement(window) is not None:
                logger.debug("[Startup] main window reveal target already resolved")
            try:
                window.show()
            except Exception:
                return
            try:
                window.bring_to_front()
            except Exception:
                pass
        self.log_startup_marker("main_window_shown_after_init")

    def hide_main_window_to_tray(self) -> dict[str, object]:
        window = self.bridge.get_window()
        tray = self.bridge.get_tray()
        if window is None:
            return {"ok": False, "message": "Window not ready"}
        if tray is None:
            return {"ok": False, "message": "Tray not available"}
        try:
            window.hide()
        except Exception as exc:
            return {"ok": False, "message": str(exc)}
        self.log_startup_marker("main_window_hidden_to_tray")
        return {"ok": True}

    def should_hide_to_tray_on_close(self) -> bool:
        return bool(self.settings.cache.get("close_to_tray_on_close", True))

    def on_main_window_closing(self, window: FolderDialogWindow) -> bool | None:
        if self.quit_in_progress:
            return None

        tray = self.bridge.get_tray()
        if self.should_hide_to_tray_on_close() and tray is not None:
            try:
                window.hide()
                self.log_startup_marker("main_window_close_redirected_to_tray")
                return False
            except Exception:
                pass

        try:
            self.bridge.quit_app()
            return False
        except Exception:
            logger.exception("Failed to quit app from main window close event")
            return None

    def _save_geometry_if_changed(self, signature: str, geometry: str, position: str, *, force: bool) -> bool:
        with self.main_geometry_lock:
            if not force and signature == self.main_geometry_last_saved:
                return False
            self.main_geometry_last_saved = signature
            self.settings.save_key("mw_size", geometry)
            self.settings.save_key("mw_pos", position)
        return True

    def save_main_window_geometry(self, force: bool = False) -> None:
        window = self.bridge.get_window()
        if window is None:
            return
        if should_skip_preloaded_geometry_save(window, show_allowed=self.main_window_show_allowed):
            return
        try:
            geometry = extract_window_placement(window)
        except Exception:
            logger.exception("[MainGeometry][save] failed to read native outer geometry")
            return
        width = geometry.width
        height = geometry.height
        x = geometry.x
        y = geometry.y

        if width >= 600 and height >= 300:
            geometry_text = format_window_size(width, height)
            position_text = format_window_position(x, y)
            signature = f"{geometry_text}@{position_text}"
            if not self._save_geometry_if_changed(signature, geometry_text, position_text, force=force):
                return
            logger.info(
                f"[MainGeometry][save] logical={geometry_text} pos={position_text} "
                f"raw_bounds={geometry.raw_x},{geometry.raw_y},{geometry.raw_width}x{geometry.raw_height} "
                f"scale_factor={geometry.scale_factor:.3f} source={geometry.source} force={force}"
            )

    def quit_app(self) -> None:
        self.quit_in_progress = True
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
