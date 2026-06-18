from __future__ import annotations

from speech_translate.window_geometry import WindowPlacement
from speech_translate.window_lifecycle import preload_window_creation
from speech_translate.webview_runtime import set_pending_window_contract


def create_preloaded_window(
    webview,
    title: str,
    url: str,
    *,
    placement: WindowPlacement,
    native_contract: dict[str, object] | None = None,
    **kwargs,
):
    reserved = {"width", "height", "x", "y"}
    duplicated_keys = reserved.intersection(kwargs)
    if duplicated_keys:
        duplicated = ", ".join(sorted(duplicated_keys))
        raise ValueError(f"create_preloaded_window received reserved overrides: {duplicated}")

    if native_contract is not None:
        set_pending_window_contract(native_contract)

    try:
        with preload_window_creation(placement) as preload_plan:
            return webview.create_window(
                title,
                url,
                width=placement.width,
                height=placement.height,
                x=preload_plan.offscreen_placement.x,
                y=preload_plan.offscreen_placement.y,
                **kwargs,
            )
    finally:
        if native_contract is not None:
            set_pending_window_contract(None)


__all__ = ["create_preloaded_window"]
