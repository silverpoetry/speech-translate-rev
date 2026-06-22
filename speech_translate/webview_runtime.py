from __future__ import annotations

from importlib import import_module
import signal
import sys
from threading import local
from typing import Literal

from speech_translate.detached_window_native import apply_initial_detached_native_contract
from speech_translate.window_lifecycle import (
    attach_preloaded_window,
    consume_pending_preloaded_window,
    get_window_lifecycle_state,
)


_pending_window_contract = local()


def _consume_pending_window_contract():
    contract = getattr(_pending_window_contract, "value", None)
    if hasattr(_pending_window_contract, "value"):
        delattr(_pending_window_contract, "value")
    return contract


def set_pending_window_contract(contract: dict[str, object] | None) -> None:
    if contract is None:
        if hasattr(_pending_window_contract, "value"):
            delattr(_pending_window_contract, "value")
        return
    _pending_window_contract.value = dict(contract)


def _patch_webview_runtime(webview_module) -> None:
    if getattr(webview_module, "_speechtranslate_window_contract_patch", False):
        return

    window_module = import_module("webview.window")
    original_window_init = window_module.Window.__init__

    def patched_window_init(self, *args, **kwargs):
        original_window_init(self, *args, **kwargs)
        contract = _consume_pending_window_contract()
        if contract is not None:
            setattr(self, "_speechtranslate_native_contract", contract)
        preload_plan = consume_pending_preloaded_window()
        if preload_plan is not None:
            attach_preloaded_window(self, preload_plan)

    window_module.Window.__init__ = patched_window_init

    try:
        winforms_module = import_module("webview.platforms.winforms")
        original_create_window = winforms_module.create_window

        def _install_sigint_support():
            handler = getattr(winforms_module, "_sigint_handler", None)
            if handler is None:
                return
            try:
                signal.signal(signal.SIGINT, handler)
            except Exception:
                pass

        def _run_master_app_loop():
            app = winforms_module.WinForms.Application
            if hasattr(winforms_module, "_sigint_received"):

                def timer_tick(sender, e):
                    if bool(getattr(winforms_module, "_sigint_received", False)):
                        app.Exit()

                timer = winforms_module.WinForms.Timer()
                timer.Interval = 500
                timer.Tick += timer_tick
                timer.Start()

            app.Run()

        def _start_winforms_app(window, create_callback):
            if window.uid == "master":
                _install_sigint_support()

                if winforms_module.is_chromium:
                    winforms_module.init_storage()

                if sys.getwindowsversion().major >= 6:
                    winforms_module.windll.user32.SetProcessDPIAware()

                if winforms_module.is_cef:
                    winforms_module.CEF.init(window, winforms_module.cache_dir)

                thread = winforms_module.Thread(winforms_module.ThreadStart(create_callback))
                thread.SetApartmentState(winforms_module.ApartmentState.STA)
                thread.Start()

                while thread.IsAlive:
                    thread.Join(500)
            else:
                winforms_module._main_window_created.wait()
                instance = list(winforms_module.BrowserView.instances.values())[0]
                instance.Invoke(winforms_module.Func[winforms_module.Type](create_callback))

        def _apply_window_icon(browser) -> None:
            try:
                from speech_translate._path import p_app_icon

                drawing = getattr(winforms_module, "Drawing", None)
                icon_type = getattr(drawing, "Icon", None) if drawing is not None else None
                if icon_type is None:
                    from System.Drawing import Icon as icon_type  # type: ignore[import-not-found]

                browser.Icon = icon_type(p_app_icon)
            except Exception:
                pass

        def _create_managed_window(window, contract):
            def create():
                browser = winforms_module.BrowserView.BrowserForm(window, winforms_module.cache_dir)
                _apply_window_icon(browser)
                winforms_module.BrowserView.instances[window.uid] = browser
                lifecycle_state = get_window_lifecycle_state(window)
                if lifecycle_state is not None and not lifecycle_state.revealed:
                    browser.Opacity = 0
                window.events.before_show.set()
                browser.Show()
                if contract is not None:
                    apply_initial_detached_native_contract(browser, contract)

                winforms_module._main_window_created.set()

                if window.uid == "master":
                    _run_master_app_loop()

            _start_winforms_app(window, create)

        def patched_create_window(window):
            contract = getattr(window, "_speechtranslate_native_contract", None)
            lifecycle_state = get_window_lifecycle_state(window)
            if contract is None and lifecycle_state is None:
                return original_create_window(window)
            kind = str((contract or {}).get("kind") or "")
            if lifecycle_state is not None or kind == "detached_window":
                return _create_managed_window(window, contract)
            return original_create_window(window)

        winforms_module.create_window = patched_create_window
    except Exception:
        pass

    setattr(webview_module, "_speechtranslate_window_contract_patch", True)


def load_webview_runtime():
    module = import_module("webview")
    _patch_webview_runtime(module)
    return module


def resolve_file_dialog(webview_module, dialog_kind: Literal["open", "folder"]):
    file_dialog = getattr(webview_module, "FileDialog", None)
    if file_dialog is None:
        raise RuntimeError("pywebview FileDialog API is unavailable; pywebview>=5.0 is required")

    if dialog_kind == "open":
        dialog = getattr(file_dialog, "OPEN", None)
    elif dialog_kind == "folder":
        dialog = getattr(file_dialog, "FOLDER", None)
    else:
        raise ValueError(f"Unsupported dialog kind: {dialog_kind}")

    if dialog is None:
        raise RuntimeError(f"pywebview FileDialog.{dialog_kind.upper()} is unavailable")
    return dialog


def create_file_dialog(
    window,
    *,
    dialog_kind: Literal["open", "folder"],
    directory: str | None = None,
    allow_multiple: bool = False,
    file_types=None,
):
    webview_module = load_webview_runtime()
    dialog = resolve_file_dialog(webview_module, dialog_kind)
    kwargs = {}
    if directory is not None:
        kwargs["directory"] = directory
    if allow_multiple:
        kwargs["allow_multiple"] = True
    if file_types is not None:
        kwargs["file_types"] = file_types
    return window.create_file_dialog(dialog, **kwargs)
