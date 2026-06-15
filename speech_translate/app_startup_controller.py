from __future__ import annotations
import sys
from dataclasses import dataclass
from pathlib import Path
from platform import processor, release, system, version
from signal import SIGINT, signal
from time import strftime, time
from typing import Callable

from speech_translate._constants import APP_NAME
from speech_translate._version import __version__
from speech_translate.app_tray import AppTray
from speech_translate.controller_protocols import FfmpegPathAdder, StartupBridge, WebviewLoader
from speech_translate.linker import sj
from speech_translate.log_helpers import logger
from speech_translate.web_bridge_runtime import WebBridgeRegistry, web_bridge_registry
from speech_translate.webview_runtime import load_webview_runtime
from speech_translate.window_geometry import resolve_window_placement


@dataclass(frozen=True)
class StartupContext:
    startup_t0: float
    tray_enabled: bool
    debug_enabled: bool
    raw_main_size: str


class AppStartupController:
    """Owns process bootstrap, main-window creation, and pywebview start orchestration."""

    def __init__(
        self,
        bridge_factory: Callable[[], StartupBridge],
        ffmpeg_path_adder: FfmpegPathAdder,
        webview_loader: WebviewLoader = load_webview_runtime,
        bridge_registry: WebBridgeRegistry = web_bridge_registry,
    ):
        self.bridge_factory = bridge_factory
        self.ffmpeg_path_adder = ffmpeg_path_adder
        self.webview_loader = webview_loader
        self.bridge_registry = bridge_registry

    def install_signal_handler(self) -> None:
        def signal_handler(_sig, _frame):
            logger.info("Received Ctrl+C, exiting...")
            bridge = self.bridge_registry.get()
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

    def _create_startup_context(self) -> StartupContext:
        return StartupContext(
            startup_t0=time(),
            tray_enabled="--no-tray" not in sys.argv,
            debug_enabled="--debug-webview" in sys.argv or "--debug" in sys.argv,
            raw_main_size=self.prepare_main_window_size(),
        )

    def _initialize_logging(self, *, with_log_init: bool, log_initializer: Callable[[str], None] | None) -> None:
        if with_log_init and log_initializer is not None:
            log_initializer(sj.cache["log_level"])

    def _log_runtime_banner(self) -> None:
        logger.info(f"App Version: {__version__} - TIME: {strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"OS: {system()} {release()} {version()} | CPU: {processor()}")
        logger.debug(f"Sys args: {sys.argv}")
        logger.debug("Loading Web UI...")

    def _initialize_webview_runtime(self):
        self.install_signal_handler()
        logger.debug("[Startup] before_add_ffmpeg")
        self.ffmpeg_path_adder(weak=True)
        logger.debug("[Startup] after_add_ffmpeg")
        logger.debug("[Startup] before_import_webview")
        webview = self.webview_loader()
        logger.debug("[Startup] after_import_webview")
        return webview

    def _create_bridge(self, startup_t0: float) -> StartupBridge:
        logger.debug("[Startup] before_bridge_init")
        bridge = self.bridge_factory()
        logger.debug("[Startup] after_bridge_init")
        bridge.set_startup_t0(startup_t0)
        self.bridge_registry.set(bridge)
        return bridge

    def _create_main_window(self, *, webview, bridge: StartupBridge, raw_main_size: str):
        main_placement = resolve_window_placement(raw_main_size, 980, 620)
        bridge.log_startup_marker("before_create_main_window")
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
        bridge.log_startup_marker("after_create_main_window")
        bridge.bind_window(window)
        return window

    def _build_webview_ready_callback(self, *, bridge: StartupBridge, tray_enabled: bool):
        def on_webview_ready() -> None:
            bridge.log_startup_marker("webview_ready_callback")
            if tray_enabled and bridge.get_tray() is None:
                try:
                    bridge.log_startup_marker("before_tray_init")
                    tray = AppTray(bridge)
                    bridge.bind_tray(tray)
                    bridge.log_startup_marker("after_tray_init")
                except Exception as exc:
                    logger.exception(exc)

        return on_webview_ready

    def start(self, with_log_init: bool = True, log_initializer: Callable[[str], None] | None = None) -> None:
        self._initialize_logging(with_log_init=with_log_init, log_initializer=log_initializer)
        self._log_runtime_banner()
        context = self._create_startup_context()
        webview = self._initialize_webview_runtime()
        bridge = self._create_bridge(context.startup_t0)
        self._create_main_window(webview=webview, bridge=bridge, raw_main_size=context.raw_main_size)
        bridge.log_startup_marker("before_webview_start")
        webview.start(
            self._build_webview_ready_callback(bridge=bridge, tray_enabled=context.tray_enabled),
            debug=context.debug_enabled,
        )
