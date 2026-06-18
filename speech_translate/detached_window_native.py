from __future__ import annotations

import ctypes
from platform import system

from speech_translate.controller_protocols import JsonDict, WebviewWindowLike
from speech_translate.detached_window_runtime import DetachedWindowDeliveryRuntime
from speech_translate.log_helpers import logger


_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_CAPTION = 0x00C00000
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
_LWA_ALPHA = 0x00000002


def get_window_hwnd(window: WebviewWindowLike | None) -> int | None:
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


def apply_native_window_settings(
    runtime: DetachedWindowDeliveryRuntime,
    mode: str,
    window: WebviewWindowLike | None,
    *,
    config: JsonDict | None = None,
) -> None:
    if system() != "Windows":
        return

    hwnd = get_window_hwnd(window)
    if hwnd is None:
        return

    if runtime.get_cached_window_style(mode) is None:
        try:
            style = int(ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_STYLE))
            ex_style = int(ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE))
            runtime.cache_window_style(mode, style, ex_style)
        except Exception:
            return

    original = runtime.get_cached_window_style(mode)
    if original is None:
        return

    cfg = config or runtime.get_pending_config(mode) or {}
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
            style &= ~(_WS_CAPTION | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX | _WS_SYSMENU | _WS_BORDER | _WS_DLGFRAME)
        if opacity < 0.999:
            ex_style |= _WS_EX_LAYERED
        else:
            ex_style &= ~_WS_EX_LAYERED

        ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_STYLE, style)
        ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex_style)

        if opacity < 0.999:
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, int(round(opacity * 255)), _LWA_ALPHA)
        else:
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, _LWA_ALPHA)

        ctypes.windll.user32.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED,
        )
    except Exception as exc:
        logger.error(f"Failed to apply detached window settings for {mode}: {exc}")


def apply_window_topmost(window: WebviewWindowLike | None, *, enabled: bool, focus_nudge: bool = False) -> None:
    if window is None:
        return

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


__all__ = [
    "apply_native_window_settings",
    "apply_window_topmost",
    "get_window_hwnd",
]
