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
_WS_EX_TRANSPARENT = 0x00000020
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


def build_detached_native_contract(config: JsonDict | None) -> JsonDict | None:
    cfg = dict(config or {})
    if not bool(cfg.get("no_title_bar", 0)):
        return None
    return {
        "kind": "detached_window",
        "no_title_bar": True,
        "opacity": cfg.get("opacity", 1.0),
        "click_through": cfg.get("click_through", 0),
    }


def _read_native_style(hwnd: int) -> tuple[int, int]:
    style = int(ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_STYLE))
    ex_style = int(ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE))
    return style, ex_style


def _resolve_base_window_style(mode: str, native_window: object | None, runtime: DetachedWindowDeliveryRuntime) -> tuple[int, int] | None:
    cached = runtime.get_cached_window_style(mode)
    if cached is not None:
        return cached
    if native_window is None:
        return None

    base_style = getattr(native_window, "_speechtranslate_base_style", None)
    base_ex_style = getattr(native_window, "_speechtranslate_base_ex_style", None)
    if base_style is not None and base_ex_style is not None:
        runtime.cache_window_style(mode, int(base_style), int(base_ex_style))
        return int(base_style), int(base_ex_style)
    return None


def _resolve_detached_window_style(base_style: int, *, no_title_bar: bool) -> int:
    if not no_title_bar:
        return int(base_style)

    return int(base_style) & ~(_WS_CAPTION | _WS_BORDER | _WS_DLGFRAME)


def _resolve_detached_ex_style(base_ex_style: int, *, opacity: float, click_through: bool) -> int:
    ex_style = int(base_ex_style)
    if opacity < 0.999 or click_through:
        ex_style |= _WS_EX_LAYERED
    else:
        ex_style &= ~_WS_EX_LAYERED
    if click_through:
        ex_style |= _WS_EX_TRANSPARENT
    else:
        ex_style &= ~_WS_EX_TRANSPARENT
    return ex_style


def _apply_style_bits(
    hwnd: int,
    *,
    style: int,
    ex_style: int,
    opacity: float,
    preserve_bounds: tuple[int, int, int, int] | None = None,
) -> None:
    ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_STYLE, int(style))
    ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, int(ex_style))

    if int(ex_style) & _WS_EX_LAYERED:
        alpha = int(round(max(0.1, min(1.0, float(opacity))) * 255))
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, alpha, _LWA_ALPHA)

    x = y = cx = cy = 0
    flags = _SWP_NOZORDER | _SWP_FRAMECHANGED
    if preserve_bounds is None:
        flags |= _SWP_NOMOVE | _SWP_NOSIZE
    else:
        x, y, cx, cy = preserve_bounds
    ctypes.windll.user32.SetWindowPos(hwnd, None, x, y, cx, cy, flags)


def apply_initial_detached_native_contract(native_window: object | None, contract: JsonDict | None) -> None:
    if system() != "Windows" or native_window is None or not contract:
        return

    try:
        handle = getattr(native_window, "Handle", None)
        hwnd = int(handle.ToInt32()) if handle is not None else None
    except Exception:
        hwnd = None
    if hwnd is None:
        return

    try:
        base_style, base_ex_style = _read_native_style(hwnd)
        setattr(native_window, "_speechtranslate_base_style", base_style)
        setattr(native_window, "_speechtranslate_base_ex_style", base_ex_style)

        target_style = _resolve_detached_window_style(base_style, no_title_bar=bool(contract.get("no_title_bar", 0)))
        target_ex_style = _resolve_detached_ex_style(
            base_ex_style,
            opacity=float(contract.get("opacity", 1.0) or 1.0),
            click_through=bool(contract.get("click_through", 0)),
        )
        bounds = getattr(native_window, "Bounds", None)
        preserve_bounds = None
        if bounds is not None:
            preserve_bounds = (
                int(getattr(bounds, "X")),
                int(getattr(bounds, "Y")),
                int(getattr(bounds, "Width")),
                int(getattr(bounds, "Height")),
            )

        _apply_style_bits(
            hwnd,
            style=target_style,
            ex_style=target_ex_style,
            opacity=float(contract.get("opacity", 1.0) or 1.0),
            preserve_bounds=preserve_bounds,
        )
    except Exception as exc:
        logger.error(f"Failed to apply initial detached native contract: {exc}")


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

    original = _resolve_base_window_style(mode, getattr(window, "native", None), runtime)
    if original is None:
        try:
            style, ex_style = _read_native_style(hwnd)
            runtime.cache_window_style(mode, style, ex_style)
            original = (style, ex_style)
        except Exception:
            return

    if original is None:
        return

    cfg = config or runtime.get_pending_config(mode) or {}
    no_title_bar = bool(cfg.get("no_title_bar", 0))
    opacity_raw = cfg.get("opacity", 1.0)
    try:
        opacity = max(0.1, min(1.0, float(opacity_raw)))
    except Exception:
        opacity = 1.0
    click_through = bool(cfg.get("click_through", 0))

    try:
        base_style, base_ex_style = original
        style = _resolve_detached_window_style(base_style, no_title_bar=no_title_bar)
        ex_style = _resolve_detached_ex_style(base_ex_style, opacity=opacity, click_through=click_through)

        native_window = getattr(window, "native", None)
        preserve_bounds = None
        bounds = getattr(native_window, "Bounds", None)
        if bounds is not None:
            preserve_bounds = (
                int(getattr(bounds, "X")),
                int(getattr(bounds, "Y")),
                int(getattr(bounds, "Width")),
                int(getattr(bounds, "Height")),
            )

        _apply_style_bits(
            hwnd,
            style=style,
            ex_style=ex_style,
            opacity=opacity,
            preserve_bounds=preserve_bounds,
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
    "apply_initial_detached_native_contract",
    "apply_native_window_settings",
    "apply_window_topmost",
    "build_detached_native_contract",
    "get_window_hwnd",
]
