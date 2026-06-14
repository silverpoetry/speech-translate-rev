from __future__ import annotations

import os
import sys
from importlib import import_module
from pathlib import Path
from platform import processor, release, system, version
from signal import SIGINT, signal
from time import strftime, time
from typing import Callable

from loguru import logger

from speech_translate._constants import APP_NAME
from speech_translate._version import __version__
from speech_translate.app_tray import AppTray
from speech_translate.controller_protocols import FfmpegPathAdder, StartupBridge, WebviewImporter
from speech_translate.linker import bc, sj
from speech_translate.window_geometry import resolve_window_placement


class AppStartupController:
    """Owns process bootstrap, main-window creation, and pywebview start orchestration."""

    def __init__(
        self,
        bridge_factory: Callable[[], StartupBridge],
        ffmpeg_path_adder: FfmpegPathAdder,
        webview_importer: WebviewImporter = import_module,
    ):
        self.bridge_factory = bridge_factory
        self.ffmpeg_path_adder = ffmpeg_path_adder
        self.webview_importer = webview_importer

    def install_signal_handler(self) -> None:
        def signal_handler(_sig, _frame):
            logger.info("Received Ctrl+C, exiting...")
            bridge = getattr(bc, "web_bridge", None)
            if bridge is not None:
                bridge.quit_app()

        signal(SIGINT, signal_handler)

    def build_html_path(self) -> str:
        return str(Path(__file__).with_name("web") / "index.html")

    def prepare_main_window_size(self) -> str:
        raw_main_size = str(sj.cache.get("mw_size", "980x620") or "980x620").strip()
        if raw_main_size == "1140x680":
            sj.save_key("mw_size", "980x620")
            raw_main_size = "980x620"
        return raw_main_size

    def start(self, with_log_init: bool = True, log_initializer: Callable[[str], None] | None = None) -> None:
        startup_t0 = time()
        if with_log_init and log_initializer is not None:
            log_initializer(sj.cache["log_level"])

        logger.info(f"App Version: {__version__} - TIME: {strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"OS: {system()} {release()} {version()} | CPU: {processor()}")
        logger.debug(f"Sys args: {sys.argv}")
        logger.debug("Loading Web UI...")

        self.install_signal_handler()
        logger.debug("[Startup] before_add_ffmpeg")
        self.ffmpeg_path_adder(weak=True)
        logger.debug("[Startup] after_add_ffmpeg")
        logger.debug("[Startup] before_import_webview")
        webview = self.webview_importer("webview")
        logger.debug("[Startup] after_import_webview")

        logger.debug("[Startup] before_bridge_init")
        bridge = self.bridge_factory()
        logger.debug("[Startup] after_bridge_init")
        bridge.set_startup_t0(startup_t0)
        setattr(bc, "web_bridge", bridge)

        tray_enabled = "--no-tray" not in sys.argv
        raw_main_size = self.prepare_main_window_size()
        main_placement = resolve_window_placement(raw_main_size, 980, 620)

        bridge._log_startup_marker("before_create_main_window")
        window = webview.create_window(
            APP_NAME,
            self.build_html_path(),
            js_api=bridge,
            width=main_placement.width,
            height=main_placement.height,
            x=main_placement.x,
            y=main_placement.y,
            min_size=(880, 560),
            hidden=True,
        )
        bridge._log_startup_marker("after_create_main_window")
        bridge.bind_window(window)

        debug_enabled = "--debug-webview" in sys.argv or "--debug" in sys.argv
        bridge._log_startup_marker("before_webview_start")

        def on_webview_ready() -> None:
            bridge._log_startup_marker("webview_ready_callback")
            if tray_enabled and bridge.get_tray() is None:
                try:
                    bridge._log_startup_marker("before_tray_init")
                    tray = AppTray(bridge)
                    bridge.bind_tray(tray)
                    bridge._log_startup_marker("after_tray_init")
                except Exception as exc:
                    logger.exception(exc)

        webview.start(on_webview_ready, debug=debug_enabled)
