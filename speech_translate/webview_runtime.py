from __future__ import annotations

from importlib import import_module
from threading import local
from typing import Literal

from speech_translate.detached_window_native import apply_initial_detached_native_contract


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

    window_module.Window.__init__ = patched_window_init

    try:
        winforms_module = import_module("webview.platforms.winforms")
        original_create_window = winforms_module.create_window

        def patched_create_window(window):
            contract = getattr(window, "_speechtranslate_native_contract", None)
            if contract is None:
                return original_create_window(window)

            def create():
                browser = winforms_module.BrowserView.BrowserForm(window, winforms_module.cache_dir)
                winforms_module.BrowserView.instances[window.uid] = browser
                window.events.before_show.set()

                if window.hidden:
                    browser.Opacity = 0
                    browser.Show()
                    apply_initial_detached_native_contract(browser, contract)
                    browser.Hide()
                    browser.Opacity = 1
                elif window.transparent and winforms_module.is_chromium:
                    browser.Show()
                    apply_initial_detached_native_contract(browser, contract)
                    browser.Hide()
                else:
                    apply_initial_detached_native_contract(browser, contract)
                    browser.Show()

                winforms_module._main_window_created.set()

                if window.uid == "master":

                    def timer_tick(sender, e):
                        if winforms_module._sigint_received:
                            app.Exit()

                    timer = winforms_module.WinForms.Timer()
                    timer.Interval = 500
                    timer.Tick += timer_tick
                    timer.Start()

                    app.Run()

            app = winforms_module.WinForms.Application

            if window.uid == "master":
                winforms_module.signal.signal(winforms_module.signal.SIGINT, winforms_module._sigint_handler)

                if winforms_module.is_chromium:
                    winforms_module.init_storage()

                if winforms_module.sys.getwindowsversion().major >= 6:
                    winforms_module.windll.user32.SetProcessDPIAware()

                if winforms_module.is_cef:
                    winforms_module.CEF.init(window, winforms_module.cache_dir)

                thread = winforms_module.Thread(winforms_module.ThreadStart(create))
                thread.SetApartmentState(winforms_module.ApartmentState.STA)
                thread.Start()

                while thread.IsAlive:
                    thread.Join(500)
            else:
                winforms_module._main_window_created.wait()
                instance = list(winforms_module.BrowserView.instances.values())[0]
                instance.Invoke(winforms_module.Func[winforms_module.Type](create))

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
