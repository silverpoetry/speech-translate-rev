from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Callable, Mapping, Protocol

from speech_translate.log_helpers import logger


class ClosableTranslator(Protocol):
    def close(self) -> None:
        ...


@dataclass(frozen=True)
class SeleniumTranslatorConfig:
    source_lang: str = "auto"
    target_lang: str = "zh-CN"
    headless: bool = False
    page_timeout_sec: float = 20.0
    force_chinese_ui: bool = True
    chrome_user_data_dir: str | None = None
    engine_compact_mode: bool = True
    engine_width: int = 420
    engine_height: int = 240
    engine_margin_right: int = 16
    engine_margin_top: int = 56
    engine_margin_bottom: int = 48
    engine_dock_bottom: bool = True
    engine_content_opacity: float = 0.75
    engine_page_zoom: float = 0.86
    win_native_compact: bool = True
    win_alpha: int = 176
    win_borderless: bool = False
    win_z_order_mode: str = "behind-main"


@dataclass(frozen=True)
class SeleniumCompactProfile:
    engine_width: int
    engine_height: int
    engine_content_opacity: float
    engine_page_zoom: float
    win_native_compact: bool
    win_alpha: int


SELENIUM_COMPACT_PROFILES: dict[int, SeleniumCompactProfile] = {
    0: SeleniumCompactProfile(
        engine_width=420,
        engine_height=240,
        engine_content_opacity=1.0,
        engine_page_zoom=1.0,
        win_native_compact=False,
        win_alpha=255,
    ),
    1: SeleniumCompactProfile(
        engine_width=360,
        engine_height=210,
        engine_content_opacity=0.92,
        engine_page_zoom=0.92,
        win_native_compact=True,
        win_alpha=220,
    ),
    2: SeleniumCompactProfile(
        engine_width=320,
        engine_height=180,
        engine_content_opacity=0.80,
        engine_page_zoom=0.86,
        win_native_compact=True,
        win_alpha=196,
    ),
    3: SeleniumCompactProfile(
        engine_width=280,
        engine_height=150,
        engine_content_opacity=0.70,
        engine_page_zoom=0.80,
        win_native_compact=True,
        win_alpha=176,
    ),
}


def resolve_selenium_compact_level(value: object) -> int:
    try:
        level = int(value)
    except Exception:
        level = 2
    return max(0, min(3, level))


def build_selenium_translator_config(settings_snapshot: Mapping[str, object]) -> SeleniumTranslatorConfig:
    level = resolve_selenium_compact_level(settings_snapshot.get("selenium_compact_level", 2))
    profile = SELENIUM_COMPACT_PROFILES[level]
    z_order_mode = str(settings_snapshot.get("selenium_z_order_mode", "behind-main") or "behind-main")
    chrome_user_data_dir = str(settings_snapshot.get("selenium_chrome_user_data_dir", "") or "").strip() or None

    return SeleniumTranslatorConfig(
        source_lang="auto",
        target_lang="zh-CN",
        headless=False,
        engine_compact_mode=True,
        engine_width=profile.engine_width,
        engine_height=profile.engine_height,
        engine_margin_right=8,
        engine_margin_top=28,
        engine_margin_bottom=40,
        engine_dock_bottom=True,
        engine_content_opacity=profile.engine_content_opacity,
        engine_page_zoom=profile.engine_page_zoom,
        win_native_compact=profile.win_native_compact,
        win_alpha=profile.win_alpha,
        win_borderless=False,
        win_z_order_mode=z_order_mode,
        chrome_user_data_dir=chrome_user_data_dir,
    )


SettingsSnapshotProvider = Callable[[], Mapping[str, object]]
SeleniumTranslatorFactory = Callable[[SeleniumTranslatorConfig], ClosableTranslator]


class SeleniumTranslatorManager:
    def __init__(
        self,
        *,
        settings_snapshot_provider: SettingsSnapshotProvider,
        translator_factory: SeleniumTranslatorFactory,
        logger_instance=logger,
    ) -> None:
        self._settings_snapshot_provider = settings_snapshot_provider
        self._translator_factory = translator_factory
        self._logger = logger_instance
        self._translator: ClosableTranslator | None = None
        self._lock = Lock()

    def get(self) -> ClosableTranslator:
        with self._lock:
            if self._translator is None:
                config = build_selenium_translator_config(self._settings_snapshot_provider())
                self._translator = self._translator_factory(config)
            return self._translator

    def shutdown(self) -> None:
        with self._lock:
            translator = self._translator
            self._translator = None

        if translator is None:
            return

        try:
            translator.close()
        except Exception as exc:
            self._logger.debug(f"Failed to close Selenium translator cleanly: {exc}")
