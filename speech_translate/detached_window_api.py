from __future__ import annotations

from typing import Any

from speech_translate.controller_protocols import JsonDict, RecordingStateProvider
from speech_translate.detached_window_settings import normalize_detached_mode
from speech_translate.log_helpers import logger


class DetachedWindowApi:
    """Minimal JS API for detached subtitle windows."""

    __slots__ = ("_manager",)

    def __init__(self, manager: Any):
        self._manager = manager

    def move_detached_window(self, mode: str, x: object, y: object) -> JsonDict:
        mode = normalize_detached_mode(mode)
        if not self._manager.has_window(mode):
            return {"status": "missing", "mode": mode}

        try:
            target_x = int(round(float(x)))
            target_y = int(round(float(y)))
        except Exception:
            return {"status": "invalid", "mode": mode}

        try:
            if not self._manager.move_window(mode, target_x, target_y):
                return {"status": "missing", "mode": mode}
            return {"status": "moved", "mode": mode, "x": target_x, "y": target_y}
        except Exception as exc:
            logger.error(f"Failed to move detached window {mode}: {exc}")
            return {"status": "error", "mode": mode, "error": str(exc)}

    def update_detached_window_geometry(self, mode: str, width: object, height: object) -> JsonDict:
        mode = normalize_detached_mode(mode)
        try:
            logical_width = int(round(float(width)))
            logical_height = int(round(float(height)))
        except Exception:
            return {"status": "invalid", "mode": mode}

        if logical_width < 200 or logical_height < 80:
            return {"status": "ignored", "mode": mode, "width": logical_width, "height": logical_height}

        try:
            self._manager.remember_window_geometry(mode, logical_width, logical_height)
            return {"status": "updated", "mode": mode, "width": logical_width, "height": logical_height}
        except Exception as exc:
            logger.error(f"Failed to remember detached window geometry {mode}: {exc}")
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


__all__ = [
    "DetachedWindowApi",
    "RecordingWindowApi",
]
