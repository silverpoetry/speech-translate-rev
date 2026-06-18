from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass
from platform import system
from typing import Any, Callable, Protocol, TypeVar


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
    x: int | None
    y: int | None
    raw_width: int | None
    raw_height: int | None
    raw_x: int | None
    raw_y: int | None
    scale_factor: float
    source: str


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

_UIResultT = TypeVar("_UIResultT")


def normalize_scale_factor(raw_scale_factor: Any) -> float:
    try:
        scale_factor = float(raw_scale_factor or 1.0)
        if scale_factor > 0:
            return scale_factor
    except Exception:
        pass
    return 1.0


def logical_to_native_size(width: int, height: int, *, scale_factor: float) -> tuple[int, int]:
    scale = normalize_scale_factor(scale_factor)
    return int(round(width * scale)), int(round(height * scale))


def native_to_logical_size(width: int, height: int, *, scale_factor: float) -> tuple[int, int]:
    scale = normalize_scale_factor(scale_factor)
    return int(round(width / scale)), int(round(height / scale))


def logical_to_physical_point(x: int, y: int, *, scale_factor: float) -> tuple[int, int]:
    scale = normalize_scale_factor(scale_factor)
    return int(round(x * scale)), int(round(y * scale))


def physical_to_logical_point(x: int, y: int, *, scale_factor: float) -> tuple[int, int]:
    scale = normalize_scale_factor(scale_factor)
    return int(round(x / scale)), int(round(y / scale))


def format_window_size(width: int, height: int) -> str:
    return f"{int(width)}x{int(height)}"


def format_window_position(x: int, y: int) -> str:
    return f"{int(x)},{int(y)}"


def _clamp_window_size(width: int, height: int) -> tuple[int, int]:
    return max(MIN_WINDOW_WIDTH, int(width)), max(MIN_WINDOW_HEIGHT, int(height))


def clamp_window_size(width: int, height: int, *, min_width: int, min_height: int) -> tuple[int, int]:
    return max(int(min_width), int(width)), max(int(min_height), int(height))


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
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
    metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER,
) -> tuple[int, int]:
    text = str(raw_value or "").strip().lower()
    match = re.match(r"^(\d+)\s*x\s*(\d+)$", text)
    if not match:
        return default_width, default_height

    width, height = clamp_window_size(int(match.group(1)), int(match.group(2)), min_width=min_width, min_height=min_height)
    return _limit_size_to_screen(width, height, metrics=metrics)


def parse_window_position(raw_value: Any) -> tuple[int | None, int | None]:
    text = str(raw_value or "").strip()
    match = re.match(r"^(-?\d+)\s*,\s*(-?\d+)$", text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def get_virtual_screen_bounds(*, metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER) -> tuple[int, int, int, int]:
    return metrics.virtual_screen_bounds()


def center_window_pos(width: int, height: int, *, metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER) -> tuple[int, int]:
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


def clamp_window_position(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER,
) -> tuple[int, int]:
    left, top, v_width, v_height = metrics.virtual_screen_bounds()
    max_x = left + max(0, v_width - max(1, width))
    max_y = top + max(0, v_height - max(1, height))
    return min(max(int(x), left), max_x), min(max(int(y), top), max_y)


def resolve_window_placement(
    raw_size: Any,
    default_width: int,
    default_height: int,
    *,
    raw_position: Any = None,
    x: int | None = None,
    y: int | None = None,
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
    metrics: MetricsProvider = DEFAULT_METRICS_PROVIDER,
) -> WindowPlacement:
    width, height = parse_window_size(
        raw_size,
        default_width,
        default_height,
        min_width=min_width,
        min_height=min_height,
        metrics=metrics,
    )
    parsed_x, parsed_y = parse_window_position(raw_position)
    if x is None:
        x = parsed_x
    if y is None:
        y = parsed_y
    if x is None or y is None:
        x, y = center_window_pos(width, height, metrics=metrics)
    x, y = ensure_visible_or_center(int(x), int(y), int(width), int(height), metrics=metrics)
    return WindowPlacement(width=width, height=height, x=x, y=y)


def _native_handle(native_window: object | None) -> int | None:
    if native_window is None:
        return None

    try:
        handle = getattr(native_window, "Handle", None)
        if handle is not None:
            if hasattr(handle, "ToInt64"):
                return int(handle.ToInt64())
            return int(handle)
    except Exception:
        pass

    try:
        return int(getattr(native_window, "handle"))
    except Exception:
        return None


def resolve_native_scale_factor(native_window: object | None) -> float:
    hwnd = _native_handle(native_window)
    if hwnd is not None and system() == "Windows":
        try:
            dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
            if dpi > 0:
                return normalize_scale_factor(dpi / 96.0)
        except Exception:
            pass

    if native_window is not None:
        try:
            device_dpi = int(getattr(native_window, "DeviceDpi"))
            if device_dpi > 0:
                return normalize_scale_factor(device_dpi / 96.0)
        except Exception:
            pass

        try:
            return normalize_scale_factor(getattr(native_window, "scale_factor", 1.0))
        except Exception:
            pass

    return DEFAULT_METRICS_PROVIDER.scale_factor()


def _read_native_bounds(native_window: object | None) -> tuple[int | None, int | None, int | None, int | None, str]:
    if native_window is None:
        return None, None, None, None, "missing"

    try:
        bounds = getattr(native_window, "Bounds", None)
        if bounds is not None:
            return (
                int(getattr(bounds, "X")),
                int(getattr(bounds, "Y")),
                int(getattr(bounds, "Width")),
                int(getattr(bounds, "Height")),
                "bounds",
            )
    except Exception:
        pass

    try:
        return (
            int(getattr(native_window, "Left")),
            int(getattr(native_window, "Top")),
            int(getattr(native_window, "Width")),
            int(getattr(native_window, "Height")),
            "left_top_size",
        )
    except Exception:
        return None, None, None, None, "unavailable"


def extract_native_window_geometry(native_window: object | None) -> NativeWindowGeometry:
    scale_factor = resolve_native_scale_factor(native_window)
    raw_x, raw_y, raw_width, raw_height, source = _read_native_bounds(native_window)
    if raw_width is None or raw_height is None:
        return NativeWindowGeometry(
            width=None,
            height=None,
            x=None,
            y=None,
            raw_width=None,
            raw_height=None,
            raw_x=raw_x,
            raw_y=raw_y,
            scale_factor=scale_factor,
            source=source,
        )

    width, height = native_to_logical_size(raw_width, raw_height, scale_factor=scale_factor)
    x, y = (None, None)
    if raw_x is not None and raw_y is not None:
        x, y = physical_to_logical_point(raw_x, raw_y, scale_factor=scale_factor)

    return NativeWindowGeometry(
        width=width,
        height=height,
        x=x,
        y=y,
        raw_width=raw_width,
        raw_height=raw_height,
        raw_x=raw_x,
        raw_y=raw_y,
        scale_factor=scale_factor,
        source=source,
    )


def extract_window_placement(window: object | None) -> NativeWindowGeometry:
    if window is None:
        raise RuntimeError("Window is unavailable")

    native_geometry = extract_native_window_geometry(getattr(window, "native", None))
    if (
        native_geometry.width is None
        or native_geometry.height is None
        or native_geometry.x is None
        or native_geometry.y is None
    ):
        raise RuntimeError(
            f"Native window bounds are unavailable (source={native_geometry.source}, "
            f"raw_bounds={native_geometry.raw_x},{native_geometry.raw_y},"
            f"{native_geometry.raw_width}x{native_geometry.raw_height})"
        )
    return native_geometry


def run_on_native_ui_thread(native_window: object | None, callback: Callable[[], _UIResultT]) -> _UIResultT:
    if native_window is None or not getattr(native_window, "InvokeRequired", False):
        return callback()

    import clr

    clr.AddReference("System")
    from System import Action

    result_box: dict[str, _UIResultT] = {}
    error_box: dict[str, Exception] = {}

    def _wrapped() -> None:
        try:
            result_box["value"] = callback()
        except Exception as exc:
            error_box["error"] = exc

    native_window.Invoke(Action(_wrapped))
    if "error" in error_box:
        raise error_box["error"]
    return result_box["value"]


def apply_native_window_placement(native_window: object | None, placement: WindowPlacement) -> bool:
    if native_window is None:
        return False

    scale_factor = resolve_native_scale_factor(native_window)
    raw_x, raw_y = logical_to_physical_point(placement.x, placement.y, scale_factor=scale_factor)
    raw_width, raw_height = logical_to_native_size(placement.width, placement.height, scale_factor=scale_factor)

    def _apply() -> bool:
        try:
            native_window.SetBounds(int(raw_x), int(raw_y), int(raw_width), int(raw_height))
            return True
        except Exception:
            native_window.Left = int(raw_x)
            native_window.Top = int(raw_y)
            native_window.Width = int(raw_width)
            native_window.Height = int(raw_height)
            return True

    return bool(run_on_native_ui_thread(native_window, _apply))


__all__ = [
    "DEFAULT_METRICS_PROVIDER",
    "MIN_VISIBLE_HEIGHT",
    "MIN_VISIBLE_WIDTH",
    "MIN_WINDOW_HEIGHT",
    "MIN_WINDOW_WIDTH",
    "NativeWindowGeometry",
    "MetricsProvider",
    "WindowPlacement",
    "WINDOW_SCREEN_MARGIN_X",
    "WINDOW_SCREEN_MARGIN_Y",
    "apply_native_window_placement",
    "clamp_window_size",
    "center_window_pos",
    "clamp_window_position",
    "ensure_visible_or_center",
    "extract_native_window_geometry",
    "extract_window_placement",
    "format_window_position",
    "format_window_size",
    "get_virtual_screen_bounds",
    "logical_to_native_size",
    "logical_to_physical_point",
    "native_to_logical_size",
    "normalize_scale_factor",
    "parse_window_position",
    "parse_window_size",
    "physical_to_logical_point",
    "resolve_native_scale_factor",
    "resolve_window_placement",
    "run_on_native_ui_thread",
]
