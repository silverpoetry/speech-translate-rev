from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import local
from typing import Iterator

from speech_translate.window_geometry import (
    WindowPlacement,
    apply_native_window_placement,
    offscreen_window_pos,
    set_native_window_opacity,
)


_WINDOW_LIFECYCLE_ATTR = "_speechtranslate_window_lifecycle"
_pending_preloaded_window = local()


@dataclass(frozen=True)
class PreloadedWindowPlan:
    target_placement: WindowPlacement
    offscreen_placement: WindowPlacement


@dataclass
class WindowLifecycleState:
    target_placement: WindowPlacement
    offscreen_placement: WindowPlacement
    revealed: bool = False


def build_offscreen_preload_placement(width: int, height: int) -> WindowPlacement:
    x, y = offscreen_window_pos(width, height)
    return WindowPlacement(width=int(width), height=int(height), x=int(x), y=int(y))


def plan_preloaded_window(target_placement: WindowPlacement) -> PreloadedWindowPlan:
    return PreloadedWindowPlan(
        target_placement=target_placement,
        offscreen_placement=build_offscreen_preload_placement(target_placement.width, target_placement.height),
    )


@contextmanager
def preload_window_creation(target_placement: WindowPlacement) -> Iterator[PreloadedWindowPlan]:
    plan = plan_preloaded_window(target_placement)
    _pending_preloaded_window.value = plan
    try:
        yield plan
    finally:
        if hasattr(_pending_preloaded_window, "value"):
            delattr(_pending_preloaded_window, "value")


def consume_pending_preloaded_window() -> PreloadedWindowPlan | None:
    plan = getattr(_pending_preloaded_window, "value", None)
    if hasattr(_pending_preloaded_window, "value"):
        delattr(_pending_preloaded_window, "value")
    return plan if isinstance(plan, PreloadedWindowPlan) else None


def attach_preloaded_window(window, plan: PreloadedWindowPlan) -> WindowLifecycleState:
    state = WindowLifecycleState(
        target_placement=plan.target_placement,
        offscreen_placement=plan.offscreen_placement,
        revealed=False,
    )
    setattr(window, _WINDOW_LIFECYCLE_ATTR, state)
    return state


def get_window_lifecycle_state(window) -> WindowLifecycleState | None:
    state = getattr(window, _WINDOW_LIFECYCLE_ATTR, None)
    return state if isinstance(state, WindowLifecycleState) else None


def is_preloaded_window(window) -> bool:
    state = get_window_lifecycle_state(window)
    return bool(state is not None and not state.revealed)


def get_target_placement(window) -> WindowPlacement | None:
    state = get_window_lifecycle_state(window)
    return state.target_placement if state is not None else None


def get_offscreen_placement(window) -> WindowPlacement | None:
    state = get_window_lifecycle_state(window)
    return state.offscreen_placement if state is not None else None


def hide_preloaded_window(window) -> bool:
    native_window = getattr(window, "native", None) if window is not None else None
    return set_native_window_opacity(native_window, 0.0)


def reveal_preloaded_window(window, *, bring_to_front: bool = True) -> bool:
    if window is None:
        return False

    state = get_window_lifecycle_state(window)
    if state is None:
        return False

    apply_native_window_placement(getattr(window, "native", None), state.target_placement)
    set_native_window_opacity(getattr(window, "native", None), 1.0)
    state.revealed = True
    if bring_to_front:
        try:
            window.bring_to_front()
        except Exception:
            pass
    return True


def should_skip_preloaded_geometry_save(window, *, show_allowed: bool = False) -> bool:
    state = get_window_lifecycle_state(window)
    return bool(state is not None and not state.revealed and not show_allowed)


__all__ = [
    "PreloadedWindowPlan",
    "WindowLifecycleState",
    "attach_preloaded_window",
    "build_offscreen_preload_placement",
    "consume_pending_preloaded_window",
    "get_offscreen_placement",
    "get_target_placement",
    "get_window_lifecycle_state",
    "hide_preloaded_window",
    "is_preloaded_window",
    "plan_preloaded_window",
    "preload_window_creation",
    "reveal_preloaded_window",
    "should_skip_preloaded_geometry_save",
]
