from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass
from platform import system
from typing import Any, Protocol


class MetricsProvider(Protocol):
    def platform_name(self) -> str:
        ...

    def screen_size(self) -> tuple[int, int]:
        ...

    def virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        ...

    def scale_factor(self) -> float:
        ...


@dataclass(frozen=True)
class WindowPlacement:
    width: int
    height: int
    x: int
    y: int


@dataclass(frozen=True)
class NativeWindowGeometry:
    width: int | None
    height: int | None
    raw_width: int | None
    raw_height: int | None
    scale_factor: float


class DefaultMetricsProvider:
    def platform_name(self) -> str:
        return system()

    def screen_size(self) -> tuple[int, int]:
        if self.platform_name() == "Windows":
            try:
                user32 = ctypes.windll.user32
                return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
            except Exception:
                pass
        left, top, width, height = self.virtual_screen_bounds()
        return left + width, top + height

    def virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        if self.platform_name() == "Windows":
            try:
                user32 = ctypes.windll.user32
                left = int(user32.GetSystemMetrics(76))
                top = int(user32.GetSystemMetrics(77))
                width = int(user32.GetSystemMetrics(78))
                height = int(user32.GetSystemMetrics(79))
                if width > 0 and height > 0:
                    return left, top, width, height
            except Exception:
                pass
        return 0, 0, 1920, 1080

    def scale_factor(self) -> float:
        if self.platform_name() == "Windows":
            try:
                scale_factor = float(ctypes.windll.shcore.GetScaleFactorForDevice(0)) / 100.0
                if scale_factor > 0:
                    return scale_factor
            except Exception:
                pass
        return 1.0


DEFAULT_METRICS_PROVIDER = DefaultMetricsProvider()
MIN_WINDOW_WIDTH = 320
MIN_WINDOW_HEIGHT = 180
WINDOW_SCREEN_MARGIN_X = 80
WINDOW_SCREEN_MARGIN_Y = 120
MIN_VISIBLE_WIDTH = 120
MIN_VISIBLE_HEIGHT = 80


def _clamp_window_size(width: int, height: int) -> tuple[int, int]:
    return max(MIN_WINDOW_WIDTH, width), max(MIN_WINDOW_HEIGHT, height)


def _limit_size_to_screen(width: int, height: int, *, metrics: MetricsProvider) -> tuple[int, int]:
    if metrics.platform_name() != "Windows":
        return width, height

    try:
        screen_width, screen_height = metrics.screen_size()
    except Exception:
        return width, height

    return (
        min(width, max(MIN_WINDOW_WIDTH, screen_width - WINDOW_SCREEN_MARGIN_X)),
        min(height, max(MIN_WINDOW_HEIGHT, screen_height - WINDOW_SCREEN_MARGIN_Y)),
    )


def _center_on_virtual_screen(width: int, height: int, *, metrics: MetricsProvider) -> tuple[int, int]:
    left, top, v_width, v_height = metrics.virtual_screen_bounds()
    centered_x = left + max(0, (v_width - max(1, width)) // 2)
    centered_y = top + max(0, (v_height - max(1, height)) // 2)
    return centered_x, centered_y


def _is_visible_enough(visible_width: int, visible_height: int) -> bool:
    return visible_width >= MIN_VISIBLE_WIDTH and visible_height >= MIN_VISIBLE_HEIGHT


def parse_window_size(
    raw_value: Any,
    default_width: int,
    default_height: int,
    *,
    metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER,
) -> tuple[int, int]:
    text = str(raw_value or "").strip().lower()
    match = re.match(r"^(\d+)\s*x\s*(\d+)$", text)
    if not match:
        return default_width, default_height

    width, height = _clamp_window_size(int(match.group(1)), int(match.group(2)))
    return _limit_size_to_screen(width, height, metrics=metrics)


def get_virtual_screen_bounds(*, metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER) -> tuple[int, int, int, int]:
    return metrics.virtual_screen_bounds()


def center_window_pos(width: int, height: int, *, metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER) -> tuple[int, int]:
    if metrics.platform_name() == "Windows":
        try:
            screen_width, screen_height = metrics.screen_size()
            scale_factor = metrics.scale_factor()
            centered_x_px = max(0, (screen_width - max(1, width)) // 2)
            centered_y_px = max(0, (screen_height - max(1, height)) // 2)
            return int(round(centered_x_px / scale_factor)), int(round(centered_y_px / scale_factor))
        except Exception:
            pass

    return _center_on_virtual_screen(width, height, metrics=metrics)


def ensure_visible_or_center(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER,
) -> tuple[int, int]:
    left, top, v_width, v_height = metrics.virtual_screen_bounds()
    right = left + max(1, v_width)
    bottom = top + max(1, v_height)

    visible_left = max(left, x)
    visible_top = max(top, y)
    visible_right = min(right, x + max(1, width))
    visible_bottom = min(bottom, y + max(1, height))
    visible_width = max(0, visible_right - visible_left)
    visible_height = max(0, visible_bottom - visible_top)

    if _is_visible_enough(visible_width, visible_height):
        return x, y

    return center_window_pos(width, height, metrics=metrics)


def resolve_window_placement(
    raw_size: Any,
    default_width: int,
    default_height: int,
    *,
    x: int | None = None,
    y: int | None = None,
    metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER,
) -> WindowPlacement:
    width, height = parse_window_size(raw_size, default_width, default_height, metrics=metrics)
    if x is None or y is None:
        x, y = center_window_pos(width, height, metrics=metrics)
    x, y = ensure_visible_or_center(int(x), int(y), int(width), int(height), metrics=metrics)
    return WindowPlacement(width=width, height=height, x=x, y=y)


def resolve_native_scale_factor(native_window: object | None) -> float:
    if native_window is None:
        return 1.0
    try:
        scale_factor = float(getattr(native_window, "scale_factor", 1.0) or 1.0)
        if scale_factor > 0:
            return scale_factor
    except Exception:
        pass
    return 1.0


def extract_native_window_geometry(native_window: object | None) -> NativeWindowGeometry:
    scale_factor = resolve_native_scale_factor(native_window)
    if native_window is None:
        return NativeWindowGeometry(None, None, None, None, scale_factor)

    try:
        client_size = getattr(native_window, "ClientSize", None)
        if client_size is None:
            return NativeWindowGeometry(None, None, None, None, scale_factor)

        raw_width = int(getattr(client_size, "Width"))
        raw_height = int(getattr(client_size, "Height"))
        width = int(round(raw_width / scale_factor))
        height = int(round(raw_height / scale_factor))
        return NativeWindowGeometry(width, height, raw_width, raw_height, scale_factor)
    except Exception:
        return NativeWindowGeometry(None, None, None, None, scale_factor)
