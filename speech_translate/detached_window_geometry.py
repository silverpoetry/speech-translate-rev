from __future__ import annotations

from typing import Optional

from speech_translate.controller_protocols import SettingsStore, WebviewWindowLike
from speech_translate.detached_window_settings import DETACHED_WINDOW_DEFAULT_GEOMETRY, build_detached_window_settings
from speech_translate.log_helpers import logger
from speech_translate.window_geometry import (
    extract_window_placement,
    format_window_position,
    format_window_size,
    WindowPlacement,
    resolve_window_placement,
)

DETACHED_MIN_WIDTH = 200
DETACHED_MIN_HEIGHT = 80


def resolve_detached_window_placement(
    settings: SettingsStore | None,
    mode: str,
    *,
    x: Optional[int],
    y: Optional[int],
    width: Optional[int],
    height: Optional[int],
) -> WindowPlacement:
    geometry_cache = DETACHED_WINDOW_DEFAULT_GEOMETRY
    position_cache = ""
    if settings is not None:
        cached = build_detached_window_settings(settings.cache, mode)
        geometry_cache = cached.geometry_cache
        position_cache = cached.position_cache

    cached_placement = resolve_window_placement(
        geometry_cache,
        900,
        240,
        raw_position=position_cache,
        min_width=DETACHED_MIN_WIDTH,
        min_height=DETACHED_MIN_HEIGHT,
    )
    resolved_geometry = format_window_size(
        int(width) if width is not None else cached_placement.width,
        int(height) if height is not None else cached_placement.height,
    )
    return resolve_window_placement(
        resolved_geometry,
        cached_placement.width,
        cached_placement.height,
        raw_position=position_cache,
        x=x,
        y=y,
        min_width=DETACHED_MIN_WIDTH,
        min_height=DETACHED_MIN_HEIGHT,
    )


def persist_detached_window_placement(
    settings: SettingsStore | None,
    mode: str,
    window: WebviewWindowLike | None,
) -> None:
    if settings is None or window is None:
        return

    try:
        geometry = extract_window_placement(window)
    except Exception:
        logger.exception(f"[DetachedGeometry][save] failed to read native outer geometry mode={mode}")
        return

    if geometry.width < 200 or geometry.height < 80:
        return

    logical_size = format_window_size(geometry.width, geometry.height)
    logical_pos = format_window_position(geometry.x, geometry.y)
    settings.save_key(f"ex_{mode}_geometry", logical_size)
    settings.save_key(f"ex_{mode}_pos", logical_pos)
    logger.info(
        f"[DetachedGeometry][save] mode={mode} logical={logical_size} pos={logical_pos} "
        f"raw_bounds={geometry.raw_x},{geometry.raw_y},{geometry.raw_width}x{geometry.raw_height} "
        f"scale_factor={geometry.scale_factor:.3f} source={geometry.source}"
    )


def log_detached_window_loaded_geometry(mode: str, window: WebviewWindowLike | None) -> None:
    if window is None:
        return

    try:
        geometry = extract_window_placement(window)
    except Exception:
        logger.exception(f"[DetachedGeometry][open-loaded] failed to read native outer geometry mode={mode}")
        return
    logger.info(
        f"[DetachedGeometry][open-loaded] mode={mode} "
        f"logical={geometry.width}x{geometry.height} pos={geometry.x},{geometry.y} "
        f"raw_bounds={geometry.raw_x},{geometry.raw_y},{geometry.raw_width}x{geometry.raw_height} "
        f"scale_factor={geometry.scale_factor:.3f} source={geometry.source}"
    )


__all__ = [
    "log_detached_window_loaded_geometry",
    "persist_detached_window_placement",
    "resolve_detached_window_placement",
]
