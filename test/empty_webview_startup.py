"""Minimal pywebview startup benchmark.

This script creates an empty webview window and logs startup timings so we can
separate engine startup cost from app logic cost.
"""

from __future__ import annotations

import argparse
from importlib import import_module
from threading import Timer
from time import perf_counter


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark startup time of an empty pywebview window")
    parser.add_argument(
        "--auto-close-seconds",
        type=float,
        default=2.0,
        help="Automatically close window after this many seconds (0 disables)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable pywebview debug mode")
    args = parser.parse_args()

    t0 = perf_counter()

    def log(marker: str) -> None:
        elapsed_ms = int((perf_counter() - t0) * 1000)
        print(f"[EmptyWebview] +{elapsed_ms}ms {marker}")

    log("start")
    webview = import_module("webview")
    log("after_import_webview")

    window = webview.create_window(
        "Empty Webview Benchmark",
        html="<html><body></body></html>",
        width=960,
        height=600,
    )
    log("after_create_window")

    if hasattr(window, "events") and hasattr(window.events, "shown"):
        window.events.shown += lambda *_: log("window_shown")
    if hasattr(window, "events") and hasattr(window.events, "loaded"):
        window.events.loaded += lambda *_: log("window_loaded")

    def on_ready() -> None:
        log("webview_ready_callback")
        if args.auto_close_seconds > 0:
            Timer(args.auto_close_seconds, lambda: window.destroy()).start()

    log("before_webview_start")
    webview.start(on_ready, debug=args.debug)
    log("after_webview_exit")


if __name__ == "__main__":
    main()
