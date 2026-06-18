from __future__ import annotations

from typing import Optional

from speech_translate.controller_protocols import SettingsStore, WebviewWindowLike
from speech_translate.detached_window_settings import DETACHED_WINDOW_DEFAULT_GEOMETRY, build_detached_window_settings
from speech_translate.log_helpers import logger
from speech_translate.window_geometry import extract_native_window_geometry, native_to_logical_size, resolve_window_placement


def resolve_detached_window_placement(
    settings: SettingsStore | None,
    mode: str,
    *,
    x: Optional[int],
    y: Optional[int],
    width: Optional[int],
    height: Optional[int],
) -> tuple[int, int, int, int]:
    geometry_cache = DETACHED_WINDOW_DEFAULT_GEOMETRY
    if settings is not None:
        geometry_cache = build_detached_window_settings(settings.cache, mode).geometry_cache

    cached_placement = resolve_window_placement(
        geometry_cache,
        900,
        240,
    )
    resolved_width = int(width) if width is not None else cached_placement.width
    resolved_height = int(height) if height is not None else cached_placement.height

    placement = resolve_window_placement(
        f"{resolved_width}x{resolved_height}",
        resolved_width,
        resolved_height,
        x=x,
        y=y,
    )
    return placement.width, placement.height, placement.x, placement.y


def _resolve_saved_window_geometry(
    window: WebviewWindowLike | None,
    *,
    fallback_width: int | None,
    fallback_height: int | None,
) -> tuple[int | None, int | None, int | None, int | None, float, str]:
    native_window = getattr(window, "native", None) if window is not None else None
    native_geometry = extract_native_window_geometry(native_window)
    raw_width = native_geometry.raw_width
    raw_height = native_geometry.raw_height
    scale_factor = native_geometry.scale_factor

    outer_width = None
    outer_height = None
    if window is not None:
        try:
            outer_width = int(getattr(window, "width"))
            outer_height = int(getattr(window, "height"))
        except Exception:
            outer_width = None
            outer_height = None

    if outer_width is not None and outer_height is not None:
        if (
            raw_width is not None
            and raw_height is not None
            and abs(outer_width - raw_width) <= max(32, int(raw_width * 0.08))
            and abs(outer_height - raw_height) <= max(32, int(raw_height * 0.12))
        ):
            logical_width, logical_height = native_to_logical_size(
                outer_width,
                outer_height,
                scale_factor=scale_factor,
            )
            return logical_width, logical_height, raw_width, raw_height, scale_factor, "outer_native_scaled"
        return outer_width, outer_height, raw_width, raw_height, scale_factor, "outer_logical"

    if fallback_width is not None and fallback_height is not None:
        return fallback_width, fallback_height, raw_width, raw_height, scale_factor, "fallback_hint"

    return native_geometry.width, native_geometry.height, raw_width, raw_height, scale_factor, "client_native_scaled"


def persist_detached_window_geometry(
    settings: SettingsStore | None,
    mode: str,
    window: WebviewWindowLike | None,
    *,
    fallback_width: int | None = None,
    fallback_height: int | None = None,
) -> None:
    if settings is None:
        return

    native_window = getattr(window, "native", None) if window is not None else None
    native_geometry = extract_native_window_geometry(native_window)
    raw_width = native_geometry.raw_width
    raw_height = native_geometry.raw_height
    scale_factor = native_geometry.scale_factor

    if fallback_width is not None and fallback_height is not None:
        width = int(fallback_width)
        height = int(fallback_height)
        source = "fallback_hint"
    elif window is not None:
        width, height, raw_width, raw_height, scale_factor, source = _resolve_saved_window_geometry(
            window,
            fallback_width=fallback_width,
            fallback_height=fallback_height,
        )
    else:
        return

    if width is None or height is None:
        return

    if width >= 200 and height >= 80:
        settings.save_key(f"ex_{mode}_geometry", f"{width}x{height}")
        logger.info(
            f"[DetachedGeometry][save] mode={mode} "
            f"saved_logical={width}x{height} raw_client={raw_width}x{raw_height} "
            f"scale_factor={scale_factor:.3f} source={source}"
        )


def log_detached_window_loaded_geometry(mode: str, window: WebviewWindowLike | None) -> None:
    if window is None:
        return

    native_window = getattr(window, "native", None)
    outer_w = None
    outer_h = None
    client_w = None
    client_h = None
    try:
        outer_w = int(getattr(window, "width"))
        outer_h = int(getattr(window, "height"))
    except Exception:
        pass
    try:
        client_size = getattr(native_window, "ClientSize", None)
        if client_size is not None:
            client_w = int(getattr(client_size, "Width"))
            client_h = int(getattr(client_size, "Height"))
    except Exception:
        pass

    scale_factor = None
    try:
        scale_factor = float(getattr(native_window, "scale_factor", 1.0) or 1.0)
    except Exception:
        pass

    logger.info(
        f"[DetachedGeometry][open-loaded] mode={mode} "
        f"outer={outer_w}x{outer_h} client={client_w}x{client_h} scale_factor={scale_factor}"
    )


__all__ = [
    "log_detached_window_loaded_geometry",
    "persist_detached_window_geometry",
    "resolve_detached_window_placement",
]
