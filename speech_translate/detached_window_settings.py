from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from speech_translate.controller_protocols import JsonDict


DETACHED_WINDOW_MODES = {"tc", "tl"}
DETACHED_WINDOW_DEFAULT_MODE = "tl"
DETACHED_WINDOW_DEFAULT_GEOMETRY = "900x240"


def normalize_detached_mode(mode: object) -> str:
    normalized = str(mode).lower()
    return normalized if normalized in DETACHED_WINDOW_MODES else DETACHED_WINDOW_DEFAULT_MODE


@dataclass(frozen=True)
class DetachedWindowConfig:
    font: object
    font_size: object
    font_bold: object
    font_color: object
    bg_color: object
    always_on_top: object
    no_title_bar: object
    opacity: object
    click_through: object

    def to_payload(self) -> JsonDict:
        return {
            "font": self.font,
            "font_size": self.font_size,
            "font_bold": self.font_bold,
            "font_color": self.font_color,
            "bg_color": self.bg_color,
            "always_on_top": self.always_on_top,
            "no_title_bar": self.no_title_bar,
            "opacity": self.opacity,
            "click_through": self.click_through,
        }


@dataclass(frozen=True)
class DetachedWindowSettings:
    mode: str
    geometry_cache: str
    config: DetachedWindowConfig


def build_detached_window_config(settings_snapshot: Mapping[str, object], mode: object) -> DetachedWindowConfig:
    normalized_mode = normalize_detached_mode(mode)
    return DetachedWindowConfig(
        font=settings_snapshot.get(f"tb_ex_{normalized_mode}_font", "Arial"),
        font_size=settings_snapshot.get(f"tb_ex_{normalized_mode}_font_size", 13),
        font_bold=settings_snapshot.get(f"tb_ex_{normalized_mode}_font_bold", True),
        font_color=settings_snapshot.get(f"tb_ex_{normalized_mode}_font_color", "#FFFFFF"),
        bg_color=settings_snapshot.get(f"tb_ex_{normalized_mode}_bg_color", "#000000"),
        always_on_top=settings_snapshot.get(f"ex_{normalized_mode}_always_on_top", 0),
        no_title_bar=settings_snapshot.get(f"ex_{normalized_mode}_no_title_bar", 0),
        opacity=settings_snapshot.get(f"ex_{normalized_mode}_opacity", 1.0),
        click_through=settings_snapshot.get(f"ex_{normalized_mode}_click_through", 0),
    )


def build_detached_window_settings(settings_snapshot: Mapping[str, object], mode: object) -> DetachedWindowSettings:
    normalized_mode = normalize_detached_mode(mode)
    return DetachedWindowSettings(
        mode=normalized_mode,
        geometry_cache=str(settings_snapshot.get(f"ex_{normalized_mode}_geometry", DETACHED_WINDOW_DEFAULT_GEOMETRY)),
        config=build_detached_window_config(settings_snapshot, normalized_mode),
    )


def get_detached_live_content(mode: object, live_state: Mapping[str, object]) -> Optional[str]:
    normalized_mode = normalize_detached_mode(mode)
    content_key = "transcribed" if normalized_mode == "tc" else "translated"
    html = live_state.get(f"detached_{content_key}_html")
    text = live_state.get(f"detached_{content_key}_text")
    if html or text:
        return str(html or text)
    return None


def detached_setting_key(mode: object, key: str) -> str:
    normalized_mode = normalize_detached_mode(mode)
    if key in ("always_on_top", "no_title_bar", "opacity", "click_through"):
        return f"ex_{normalized_mode}_{key}"
    return f"tb_ex_{normalized_mode}_{key}"


__all__ = [
    "DETACHED_WINDOW_DEFAULT_GEOMETRY",
    "DETACHED_WINDOW_DEFAULT_MODE",
    "DETACHED_WINDOW_MODES",
    "DetachedWindowConfig",
    "DetachedWindowSettings",
    "build_detached_window_config",
    "build_detached_window_settings",
    "detached_setting_key",
    "get_detached_live_content",
    "normalize_detached_mode",
]
